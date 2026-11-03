'''
# Lab: LERIS/UFSCar
# GCC (Google Congestion Control) driven by RTCP/TWCC feedback.
#
# Used by cg_server1.py on the UDP/RTP (GStreamer) path. GStreamer's rtpbin
# stamps every outgoing RTP packet with a transport-wide sequence number
# (TWCC header extension), the player's rtpbin answers with RTCP TWCC feedback
# (RTPFB, FMT=15), and the server's rtpsession turns that feedback into an
# upstream "RTPTWCCPackets" event (one entry per packet: send time, arrival
# time, size, lost). This module consumes those entries and computes a
# network-driven target bitrate, following draft-ietf-rmcat-gcc-02:
#
#   * delay-based controller : inter-group delay variation -> trendline filter
#                              -> adaptive-threshold overuse detector -> AIMD
#   * loss-based controller  : >10% loss -> decrease, <2% loss -> increase
#   * target = min(delay-based, loss-based)
#
# The result is only an upper bound coming from the network. The application-
# layer (frame/command sync) rate control in cg_server1.py stays untouched; the
# server feeds min(app_rate, gcc_rate) to the encoder.
'''

import math
import threading
import time
from collections import deque

TWCC_URI = "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"
_CLOCK_NONE = (1 << 64) - 1      # GST_CLOCK_TIME_NONE
_OVERHEAD_BYTES = 40             # IPv4 (20) + UDP (8) + RTP (12) per packet


class _TrendlineEstimator:
    """Delay-gradient trend (libwebrtc TrendlineEstimator)."""

    def __init__(self, window=20, smoothing=0.9, gain=4.0):
        self.window = window
        self.smoothing = smoothing
        self.gain = gain
        self.hist = deque(maxlen=window)
        self.acc_delay = 0.0
        self.smoothed_delay = 0.0
        self.first_arrival_ms = None
        self.num_deltas = 0
        self.prev_trend = 0.0

    def update(self, recv_delta_ms, send_delta_ms, arrival_ms):
        self.num_deltas = min(self.num_deltas + 1, 1000)
        if self.first_arrival_ms is None:
            self.first_arrival_ms = arrival_ms
        self.acc_delay += recv_delta_ms - send_delta_ms
        self.smoothed_delay = (self.smoothing * self.smoothed_delay
                               + (1.0 - self.smoothing) * self.acc_delay)
        self.hist.append((arrival_ms - self.first_arrival_ms, self.smoothed_delay))
        trend = self.prev_trend
        if len(self.hist) == self.window:
            xs = [p[0] for p in self.hist]
            ys = [p[1] for p in self.hist]
            mx = sum(xs) / len(xs)
            my = sum(ys) / len(ys)
            den = sum((x - mx) ** 2 for x in xs)
            if den > 0:
                trend = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        self.prev_trend = trend
        return min(self.num_deltas, 60) * trend * self.gain


class _OveruseDetector:
    """Adaptive-threshold overuse detector (GCC section 5.4)."""

    K_UP, K_DOWN = 0.0087, 0.039
    OVERUSE_TIME_MS = 10.0

    def __init__(self):
        self.threshold = 12.5
        self.last_update_ms = None
        self.time_over_using = -1.0
        self.overuse_counter = 0
        self.prev_trend = 0.0
        self.state = "normal"

    def detect(self, modified_trend, ts_delta_ms, now_ms):
        if modified_trend > self.threshold:
            if self.time_over_using == -1.0:
                self.time_over_using = ts_delta_ms / 2.0
            else:
                self.time_over_using += ts_delta_ms
            self.overuse_counter += 1
            if (self.time_over_using > self.OVERUSE_TIME_MS and self.overuse_counter > 1
                    and modified_trend >= self.prev_trend):
                self.time_over_using = 0.0
                self.overuse_counter = 0
                self.state = "overuse"
        elif modified_trend < -self.threshold:
            self.time_over_using = -1.0
            self.overuse_counter = 0
            self.state = "underuse"
        else:
            self.time_over_using = -1.0
            self.overuse_counter = 0
            self.state = "normal"
        self.prev_trend = modified_trend
        self._adapt(modified_trend, now_ms)
        return self.state

    def _adapt(self, modified_trend, now_ms):
        if self.last_update_ms is None:
            self.last_update_ms = now_ms
        abs_t = abs(modified_trend)
        if abs_t > self.threshold + 15.0:      # spike: do not adapt
            self.last_update_ms = now_ms
            return
        k = self.K_DOWN if abs_t < self.threshold else self.K_UP
        dt = min(now_ms - self.last_update_ms, 100.0)
        self.threshold += k * (abs_t - self.threshold) * dt
        self.threshold = min(max(self.threshold, 6.0), 600.0)
        self.last_update_ms = now_ms


class GCCController:
    """Thread-safe GCC estimator. Feed it TWCC packet reports with on_twcc_packets()."""

    BURST_MS = 5.0          # packets sent within 5 ms form one group
    BETA = 0.85             # multiplicative decrease factor
    ACK_WINDOW_MS = 500.0   # acknowledged-bitrate window

    def __init__(self, start_kbps, min_kbps, max_kbps, log_path=None):
        self.min_bps = min_kbps * 1000.0
        self.max_bps = max_kbps * 1000.0
        start = min(max(start_kbps * 1000.0, self.min_bps), self.max_bps)
        self.delay_bps = start
        self.loss_bps = start
        self.target_bps = start
        self.rate_state = "increase"     # AIMD state: increase / hold / decrease
        self.signal = "normal"
        self.last_rate_update = None
        self.last_decrease = None
        self.link_capacity = None        # bps, EWMA of acked rate at decreases
        self.acked_bps = None
        self.loss_ratio = 0.0
        self.feedback_count = 0
        self.last_loss_update = None
        self.last_loss_decrease = None
        self._seen_received = False      # ignore "losses" before the receiver was up

        self._trend = _TrendlineEstimator()
        self._detector = _OveruseDetector()
        self._cur_group = None           # [first_send, last_send, last_arrival]
        self._prev_group = None
        self._acked = deque()            # (arrival_ms, bytes)
        self._loss_recv = 0
        self._loss_lost = 0
        self._lock = threading.Lock()
        self._log_path = log_path
        if log_path:
            with open(log_path, "w") as f:
                f.write("time,feedback_id,packets,lost,acked_kbps,modified_trend,threshold,"
                        "signal,rate_state,delay_kbps,loss_kbps,loss_ratio,target_kbps\n")

    # -- public API ----------------------------------------------------------
    def get_target_kbps(self):
        with self._lock:
            return self.target_bps / 1000.0

    def on_twcc_packets(self, packets):
        """packets: iterable of dicts {seqnum, local_ts, remote_ts, size, lost} (ns)."""
        now = time.perf_counter()
        with self._lock:
            pkts = sorted(packets, key=lambda p: p["local_ts"])
            n_lost = 0
            mtrend = None
            for p in pkts:
                if p["lost"] or p["remote_ts"] is None:
                    if self._seen_received:
                        n_lost += 1
                    continue
                self._seen_received = True
                send_ms = p["local_ts"] / 1e6
                arr_ms = p["remote_ts"] / 1e6
                self._acked.append((arr_ms, p["size"] + _OVERHEAD_BYTES))
                r = self._on_packet(send_ms, arr_ms)
                if r is not None:
                    mtrend = r
            n_recv = sum(1 for p in pkts if not (p["lost"] or p["remote_ts"] is None))
            self._loss_recv += n_recv
            self._loss_lost += n_lost
            self._update_acked()
            self._update_delay_based(now)
            self._update_loss_based(now)
            self.target_bps = min(max(min(self.delay_bps, self.loss_bps), self.min_bps), self.max_bps)
            self.feedback_count += 1
            self._log(now, len(pkts), n_lost, mtrend)

    # -- delay-based ---------------------------------------------------------
    def _on_packet(self, send_ms, arr_ms):
        g = self._cur_group
        if g is None:
            self._cur_group = [send_ms, send_ms, arr_ms]
            return None
        if send_ms < g[0]:                       # reordered/old packet: ignore
            return None
        if send_ms - g[0] <= self.BURST_MS:      # same group
            g[1] = max(g[1], send_ms)
            g[2] = max(g[2], arr_ms)
            return None
        # current group complete -> compare with previous complete group
        result = None
        if self._prev_group is not None:
            send_delta = g[1] - self._prev_group[1]
            recv_delta = g[2] - self._prev_group[2]
            if abs(recv_delta - send_delta) < 3000.0:   # drop clock jumps / stale groups
                mtrend = self._trend.update(recv_delta, send_delta, g[2])
                self.signal = self._detector.detect(mtrend, max(send_delta, 0.0), g[2])
                result = mtrend
        self._prev_group = g
        self._cur_group = [send_ms, send_ms, arr_ms]
        return result

    def _update_acked(self):
        if not self._acked:
            return
        newest = self._acked[-1][0]
        while self._acked and newest - self._acked[0][0] > self.ACK_WINDOW_MS:
            self._acked.popleft()
        span = newest - self._acked[0][0]
        if span >= 50.0:
            bits = sum(b for _, b in self._acked) * 8.0
            self.acked_bps = bits / (span / 1000.0)

    def _update_delay_based(self, now):
        # AIMD state machine driven by the detector signal (GCC section 5.5)
        if self.signal == "overuse":
            self.rate_state = "decrease"
        elif self.signal == "underuse":
            self.rate_state = "hold"
        elif self.rate_state == "hold":
            self.rate_state = "increase"
        elif self.rate_state == "decrease":
            self.rate_state = "hold"

        dt = 0.0 if self.last_rate_update is None else min(now - self.last_rate_update, 1.0)
        self.last_rate_update = now
        rate = self.delay_bps

        if self.rate_state == "increase":
            near_capacity = (self.link_capacity is not None and self.acked_bps is not None
                             and abs(self.acked_bps - self.link_capacity) < 0.3 * self.link_capacity)
            if near_capacity:
                # additive: ~one 1200-byte packet per response time (~200 ms)
                rate += max(1000.0, 1200 * 8 * dt / 0.2)
            else:
                rate *= math.pow(1.08, dt)       # multiplicative: +8 %/s
        elif self.rate_state == "decrease":
            if self.last_decrease is None or now - self.last_decrease > 0.2:
                base = self.acked_bps if self.acked_bps is not None else rate
                rate = min(rate, self.BETA * base)
                self.link_capacity = (base if self.link_capacity is None
                                      else 0.95 * self.link_capacity + 0.05 * base)
                self.last_decrease = now
            self.rate_state = "hold"

        if self.acked_bps is not None:           # never run far ahead of what arrives
            rate = min(rate, 1.5 * self.acked_bps + 10000.0)
        self.delay_bps = min(max(rate, self.min_bps), self.max_bps)

    # -- loss-based ----------------------------------------------------------
    LOSS_DECREASE_GUARD_S = 0.3   # at most one loss-based decrease per ~RTT + 300 ms

    def _update_loss_based(self, now):
        total = self._loss_recv + self._loss_lost
        if total < 20:                           # wait for enough samples
            return
        p = self._loss_lost / float(total)
        self.loss_ratio = p
        dt = 0.0 if self.last_loss_update is None else min(now - self.last_loss_update, 1.0)
        self.last_loss_update = now
        if p > 0.10:
            if self.last_loss_decrease is None or now - self.last_loss_decrease > self.LOSS_DECREASE_GUARD_S:
                self.loss_bps *= (1.0 - 0.5 * p)
                self.last_loss_decrease = now
        elif p < 0.02:
            self.loss_bps *= math.pow(1.08, dt)  # +8 %/s while loss is low
        if self.acked_bps is not None:           # never run far ahead of what arrives
            self.loss_bps = min(self.loss_bps, 1.5 * self.acked_bps + 10000.0)
        self.loss_bps = min(max(self.loss_bps, self.min_bps), self.max_bps)
        self._loss_recv = self._loss_lost = 0

    # -- logging -------------------------------------------------------------
    def _log(self, now, n, lost, mtrend):
        if not self._log_path:
            return
        acked = "" if self.acked_bps is None else f"{self.acked_bps / 1000.0:.1f}"
        mt = "" if mtrend is None else f"{mtrend:.3f}"
        with open(self._log_path, "a") as f:
            f.write(f"{now},{self.feedback_count},{n},{lost},{acked},{mt},"
                    f"{self._detector.threshold:.3f},{self.signal},{self.rate_state},"
                    f"{self.delay_bps / 1000.0:.1f},{self.loss_bps / 1000.0:.1f},"
                    f"{self.loss_ratio:.4f},{self.target_bps / 1000.0:.1f}\n")


def attach_twcc(Gst, GstRtp, pay, controller, ext_id=1):
    """Enable the TWCC header extension on `pay` and route the TWCC feedback
    (upstream "RTPTWCCPackets" events from rtpbin) into `controller`.
    Returns True on success, False if this GStreamer cannot do TWCC."""
    ext = GstRtp.RTPHeaderExtension.create_from_uri(TWCC_URI)
    if ext is None:
        return False
    ext.set_id(ext_id)
    pay.emit("add-extension", ext)

    def _probe(pad, info):
        ev = info.get_event()
        if ev is not None and ev.type == Gst.EventType.CUSTOM_UPSTREAM:
            s = ev.get_structure()
            if s is not None and s.get_name() == "RTPTWCCPackets":
                pkts = []
                for ps in (s.get_value("packets") or []):
                    rts = ps.get_value("remote-ts") if ps.has_field("remote-ts") else None
                    if rts == _CLOCK_NONE:
                        rts = None
                    pkts.append({
                        "seqnum": ps.get_value("seqnum"),
                        "local_ts": ps.get_value("local-ts"),
                        "remote_ts": rts,
                        "size": ps.get_value("size") or 0,
                        "lost": bool(ps.get_value("lost")),
                    })
                if pkts:
                    try:
                        controller.on_twcc_packets(pkts)
                    except Exception as e:   # never break the streaming thread
                        print(f"[GCC] feedback processing error: {e}")
        return Gst.PadProbeReturn.OK

    pay.get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_UPSTREAM, _probe)
    return True
