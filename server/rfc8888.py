'''
# Lab: LERIS/UFSCar
# RFC 8888 congestion-control feedback (CCFB) — SERVER side.
#
# RFC 8888 = "RTP Control Protocol (RTCP) Feedback for Congestion Control":
# RTCP RTPFB (PT=205), FMT=11. Per RTP stream (SSRC) the receiver reports, for
# every RTP sequence number in [begin_seq, begin_seq+num_reports), whether it
# arrived (R bit), its ECN bits and its arrival-time offset (ATO, 1/1024 s)
# relative to the report timestamp (middle 32 bits of NTP).
#
# GStreamer has no RFC 8888 implementation, so here:
#   * SendTracker  : buffer probe on the payloader src pad -> remembers the send
#                    time and size of every RTP packet, keyed by RTP seqnum
#   * Rfc8888Server: thread listening for CCFB packets from the player; it matches
#                    every report with the SendTracker and feeds the result to the
#                    same GCCController used by the TWCC mode.
# The player side lives in player/rfc8888_receiver.py.
'''

import socket
import struct
import threading
import time

RTPFB_PT = 205
CCFB_FMT = 11
ATO_OVERRANGE = 0x1FFF


def parse_ccfb(data):
    """Parse one compound RTCP datagram, return list of (report_ts_s, ssrc, [(seq, rcv, ecn, ato)])."""
    out = []
    off = 0
    while off + 8 <= len(data):
        b0, pt, length = struct.unpack_from("!BBH", data, off)
        plen = (length + 1) * 4
        if (b0 >> 6) != 2 or off + plen > len(data):
            break
        if pt == RTPFB_PT and (b0 & 0x1F) == CCFB_FMT and plen >= 12:
            body = data[off + 8: off + plen]          # after header + sender SSRC
            report_ts = struct.unpack_from("!I", body, len(body) - 4)[0] / 65536.0
            p = 0
            blocks = body[:-4]
            while p + 8 <= len(blocks):
                ssrc, begin_seq, num = struct.unpack_from("!IHH", blocks, p)
                p += 8
                reports = []
                for i in range(num):
                    if p + 2 > len(blocks):
                        break
                    (v,) = struct.unpack_from("!H", blocks, p)
                    p += 2
                    reports.append(((begin_seq + i) & 0xFFFF, bool(v >> 15), (v >> 13) & 0x3, v & 0x1FFF))
                if num % 2:
                    p += 2                            # 32-bit padding
                out.append((report_ts, ssrc, reports))
        off += plen
    return out


class SendTracker:
    """Remembers (send_time_ns, size) per RTP seqnum, from a payloader src-pad probe."""

    def __init__(self, keep=8192):
        self._sent = {}
        self._order = []
        self._keep = keep
        self._lock = threading.Lock()

    def _record(self, buf):
        size = buf.get_size()
        if size < 12:
            return
        hdr = buf.extract_dup(0, 4)
        seq = struct.unpack("!H", hdr[2:4])[0]
        now = time.perf_counter_ns()
        with self._lock:
            self._sent[seq] = (now, size - 12)
            self._order.append(seq)
            if len(self._order) > self._keep:
                old = self._order.pop(0)
                if self._sent.get(old, (0,))[0] <= now - 5_000_000_000:
                    self._sent.pop(old, None)

    def attach(self, Gst, pad):
        def _probe(pad, info):
            if info.type & Gst.PadProbeType.BUFFER_LIST:
                lst = info.get_buffer_list()
                for i in range(lst.length()):
                    self._record(lst.get(i))
            else:
                self._record(info.get_buffer())
            return Gst.PadProbeReturn.OK
        pad.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST, _probe)

    def pop(self, seq):
        with self._lock:
            return self._sent.pop(seq, None)


class Rfc8888Server(threading.Thread):
    """Receives RFC 8888 CCFB from the player and drives the GCC controller."""

    def __init__(self, bind_ip, port, tracker, controller):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind_ip, port))
        self.tracker = tracker
        self.controller = controller

    def run(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(65535)
            except OSError:
                return
            pkts = []
            for report_ts, _ssrc, reports in parse_ccfb(data):
                for seq, rcv, _ecn, ato in reports:
                    sent = self.tracker.pop(seq)
                    if sent is None:                  # unknown / already reported
                        continue
                    if rcv and ato != ATO_OVERRANGE:
                        remote = int((report_ts - ato / 1024.0) * 1e9)
                    else:
                        remote = None
                    pkts.append({"seqnum": seq, "local_ts": sent[0], "remote_ts": remote,
                                 "size": sent[1], "lost": not rcv})
            if pkts:
                try:
                    self.controller.on_twcc_packets(pkts)   # same GCC as the TWCC mode
                except Exception as e:
                    print(f"[RFC8888] feedback processing error: {e}")
