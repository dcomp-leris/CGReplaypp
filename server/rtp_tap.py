'''
# Lab: LERIS/UFSCar
# Sender-side RTP tap for the GStreamer server (UDP/RTP "WebRTC-style" in every
# congestion_control.feedback mode, and SCReAM).
#
# It ties every RTP packet that leaves the server to the video frame it carries:
#
#   main loop  frame_pushed(buf, frame_id, qr_ts) -> tags the raw GstBuffer (buf.offset = instance)
#   encoder    sink-pad probe                     -> instance read back from buf.offset (videoconvert
#                                                    copies it), queued in encoding order
#   payloader  src-pad probe                      -> each new RTP timestamp = next encoded frame
#                                                    (x264/x265 zerolatency: 1 access unit per input,
#                                                    no reordering; x264enc rewrites PTS, so PTS is
#                                                    NOT usable as the key)
#   udpsink    sink-pad probe (what goes on the wire, after rtpbin / screamtx)
#                                               -> one log row per RTP packet
#
# The RTP timestamp is the join key: all packets of one video frame share it, the
# last one has the marker bit, and the network pcaps / player are joined on it.
#
# Logs (server/logs/):
#   srv_video_frames.csv : one row per pushed frame instance (frame_id, QR timestamp,
#                          push time, PTS, RTP timestamp, bitrates)
#   srv_rtp.csv          : one row per RTP packet sent (rtp_parse.PKT_COLS)
'''

import atexit
import collections
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from rtp_parse import PKT_COLS, describe_rtp, pkt_row   # noqa: E402

FRAME_COLS = ["instance", "frame_id", "qr_ts", "push_wall", "push_perf", "pts_ns", "rtp_ts",
              "app_bitrate", "applied_bitrate", "raw_bytes"]


class RtpTap:
    def __init__(self, Gst, pipeline, codec="h264", rtp_dst_port=None,
                 pkt_log="./logs/srv_rtp.csv", frame_log="./logs/srv_video_frames.csv"):
        self.Gst = Gst
        self.codec = codec
        self._lock = threading.Lock()
        self._pending = {}                         # instance -> frame info (pushed, not yet encoded)
        self._encoded = collections.deque()        # frames in encoding order, waiting for an RTP ts
        self._last_ts = None
        self._by_ts = collections.OrderedDict()   # rtp_ts -> frame info
        self._instance = 0
        self._pkt_f = open(pkt_log, "w", buffering=1 << 16)
        self._frm_f = open(frame_log, "w", buffering=1 << 14)
        self._pkt_f.write(",".join(PKT_COLS) + "\n")
        self._frm_f.write(",".join(FRAME_COLS) + "\n")
        self._n = 0
        atexit.register(self.close)

        enc = pay = sink = None
        it = pipeline.iterate_recurse()
        while True:
            ok, el = it.next()
            if ok != Gst.IteratorResult.OK:
                break
            fac = el.get_factory().get_name() if el.get_factory() else ""
            if fac in ("x264enc", "x265enc", "nvh264enc", "vaapih264enc", "openh264enc"):
                enc = el
            elif fac.startswith("rtph26") and fac.endswith("pay"):
                pay = el
            elif fac == "udpsink" and rtp_dst_port is not None and el.get_property("port") == rtp_dst_port:
                sink = el
        if enc is None or pay is None:
            print("[RTP-TAP] encoder/payloader not found -> RTP tap disabled")
            self.ok = False
            return
        self.ok = True
        enc.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, self._on_raw)
        pay.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST,
                                            self._on_pay)
        wire = sink.get_static_pad("sink") if sink is not None else pay.get_static_pad("src")
        wire.add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST, self._on_wire)
        print(f"[RTP-TAP] logging RTP at {'udpsink (wire)' if sink else 'payloader'}: "
              f"{enc.get_name()} -> {pay.get_name()}")

    # ---- called by the main loop right before appsrc push-buffer ----------------
    def frame_pushed(self, buf, frame_id, qr_ts, app_bitrate="", applied_bitrate="", raw_bytes=""):
        with self._lock:
            self._instance += 1
            buf.offset = self._instance              # survives appsrc -> videoconvert -> encoder sink
            self._pending[self._instance] = ({"instance": self._instance, "frame_id": frame_id, "qr_ts": qr_ts,
                                  "push_wall": time.time(), "push_perf": time.perf_counter(),
                                  "app_bitrate": app_bitrate, "applied_bitrate": applied_bitrate,
                                  "raw_bytes": raw_bytes})

    # ---- probes ------------------------------------------------------------------
    def _on_raw(self, pad, info):
        buf = info.get_buffer()
        with self._lock:
            fr = self._pending.pop(buf.offset, None)
            if fr is not None:
                fr["pts_ns"] = buf.pts
                self._encoded.append(fr)
                for k in [k for k in self._pending if k < buf.offset]:   # never reached the encoder
                    self._pending.pop(k)
        return self.Gst.PadProbeReturn.OK

    def _iter(self, info):
        if info.type & self.Gst.PadProbeType.BUFFER_LIST:
            lst = info.get_buffer_list()
            return [lst.get(i) for i in range(lst.length())]
        return [info.get_buffer()]

    def _on_pay(self, pad, info):
        for buf in self._iter(info):
            if buf.get_size() < 12:
                continue
            ts = struct.unpack("!I", buf.extract_dup(4, 4))[0]
            with self._lock:
                if ts == self._last_ts or ts in self._by_ts:
                    continue
                self._last_ts = ts
                if not self._encoded:
                    continue
                fr = self._encoded.popleft()
                fr["rtp_ts"] = ts
                self._by_ts[ts] = fr
                while len(self._by_ts) > 4096:
                    self._by_ts.popitem(last=False)
                self._frm_f.write(",".join(str(fr.get(c, "")) for c in FRAME_COLS) + "\n")
        return self.Gst.PadProbeReturn.OK

    def _on_wire(self, pad, info):
        now = time.time()
        for buf in self._iter(info):
            data = buf.extract_dup(0, min(buf.get_size(), 64))
            r = describe_rtp(data, self.codec)
            if r is None:
                continue
            r["pkt_len"] = buf.get_size()
            r["payload_len"] = buf.get_size() - r["hdr_len"]
            with self._lock:
                fr = self._by_ts.get(r["ts"], {})
                self._pkt_f.write(pkt_row("server_tx", now, r, fr.get("frame_id", ""),
                                          fr.get("instance", "")) + "\n")
                self._n += 1
                if self._n % 200 == 0:
                    self._pkt_f.flush(); self._frm_f.flush()
        return self.Gst.PadProbeReturn.OK

    def close(self):
        try:
            with self._lock:
                self._pkt_f.flush(); self._frm_f.flush()
        except Exception:
            pass
