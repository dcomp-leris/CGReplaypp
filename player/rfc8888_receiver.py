'''
# Lab: LERIS/UFSCar
# RFC 8888 congestion-control feedback (CCFB) — PLAYER side.
#
# The player decodes through cv2.VideoCapture(GStreamer), which gives Python no
# access to per-packet arrival times. So in RFC 8888 mode a small relay thread
# owns the streaming port: it timestamps every RTP packet (and reads its ECN bits),
# forwards it unchanged to a local port where the GStreamer pipeline listens, and
# every `interval_ms` sends an RTCP RTPFB (PT=205, FMT=11) CCFB report (RFC 8888)
# to the server, which runs GCC on it (server/rfc8888.py).
'''

import socket
import struct
import threading
import time

RTPFB_PT = 205
CCFB_FMT = 11
_NTP_EPOCH_OFFSET = 2208988800
_MAX_REPORTS = 16384


def _ntp_mid32(t_unix):
    """Middle 32 bits of the 64-bit NTP timestamp (16.16 fixed point seconds)."""
    return int((t_unix + _NTP_EPOCH_OFFSET) * 65536.0) & 0xFFFFFFFF


def build_ccfb(sender_ssrc, streams, report_unix):
    """streams: {ssrc: (begin_seq, [(rcv, ecn, arrival_unix) or None per seq])} -> bytes."""
    ts = _ntp_mid32(report_unix)
    body = b""
    for ssrc, (begin_seq, reps) in streams.items():
        blk = struct.pack("!IHH", ssrc, begin_seq & 0xFFFF, len(reps))
        for r in reps:
            if r is None:
                blk += b"\x00\x00"
            else:
                _, ecn, arr = r
                ato = int(round((report_unix - arr) * 1024.0))
                ato = 0x1FFF if ato >= 0x1FFF else max(ato, 0)   # 0x1FFF = over-range
                blk += struct.pack("!H", 0x8000 | ((ecn & 3) << 13) | ato)
        if len(reps) % 2:
            blk += b"\x00\x00"
        body += blk
    body += struct.pack("!I", ts)
    length = (8 + len(body)) // 4 - 1
    return struct.pack("!BBHI", 0x80 | CCFB_FMT, RTPFB_PT, length, sender_ssrc) + body


class Rfc8888Relay(threading.Thread):
    """listen_ip:listen_port (RTP from server) -> 127.0.0.1:fwd_port (GStreamer udpsrc).
    Sends RFC 8888 CCFB to (server_ip, server_port) every interval_ms."""

    def __init__(self, listen_ip, listen_port, fwd_port, server_ip, server_port, interval_ms=33):
        super().__init__(daemon=True)
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)   # same as send_command()
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self.ecn_ok = hasattr(socket, "IP_RECVTOS")
        if self.ecn_ok:
            self.rx.setsockopt(socket.IPPROTO_IP, socket.IP_RECVTOS, 1)
        self.rx.bind((listen_ip, listen_port))
        self.rx.settimeout(interval_ms / 1000.0)
        self.fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.fwd_addr = ("127.0.0.1", fwd_port)
        self.fb = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.fb_addr = (server_ip, server_port)
        self.interval = interval_ms / 1000.0
        self.ssrc = 0x43475250            # "CGRP"
        self.pending = {}                  # ssrc -> {ext_seq: (rcv, ecn, arrival)}
        self.next_seq = {}                 # ssrc -> next extended seq to report
        self.highest = {}                  # ssrc -> highest extended seq seen
        self.reports_sent = 0

    def _ext(self, ssrc, seq):
        hi = self.highest.get(ssrc)
        if hi is None:
            return seq
        cand = (hi & ~0xFFFF) | seq
        if cand < hi - 0x8000:
            cand += 0x10000
        elif cand > hi + 0x8000:
            cand -= 0x10000
        return cand

    def run(self):
        last_fb = time.time()
        while True:
            try:
                data, anc, _, _ = self.rx.recvmsg(65535, socket.CMSG_SPACE(4))
                now = time.time()
                self.fwd.sendto(data, self.fwd_addr)
                if len(data) >= 12 and (data[0] >> 6) == 2:
                    ecn = 0
                    for lvl, typ, val in anc:
                        if lvl == socket.IPPROTO_IP and val:
                            ecn = val[0] & 0x3
                    seq, = struct.unpack_from("!H", data, 2)
                    ssrc, = struct.unpack_from("!I", data, 8)
                    es = self._ext(ssrc, seq)
                    self.highest[ssrc] = max(es, self.highest.get(ssrc, es))
                    self.next_seq.setdefault(ssrc, es)
                    if es >= self.next_seq[ssrc]:
                        self.pending.setdefault(ssrc, {})[es] = (True, ecn, now)
            except socket.timeout:
                now = time.time()
            except OSError:
                return
            if now - last_fb >= self.interval:
                last_fb = now
                self._send_report(now)

    def _send_report(self, now):
        streams = {}
        for ssrc, pend in self.pending.items():
            if not pend:
                continue
            begin = self.next_seq[ssrc]
            end = self.highest[ssrc]
            if end - begin + 1 > _MAX_REPORTS:
                begin = end - _MAX_REPORTS + 1
            streams[ssrc] = (begin, [pend.get(s) for s in range(begin, end + 1)])
            self.next_seq[ssrc] = end + 1
            self.pending[ssrc] = {}
        if streams:
            try:
                self.fb.sendto(build_ccfb(self.ssrc, streams, now), self.fb_addr)
                self.reports_sent += 1
            except OSError:
                pass
