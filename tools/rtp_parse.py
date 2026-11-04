'''
# Lab: LERIS/UFSCar
# Shared RTP / H.264 / H.265 / pcap parsing for the RTP-frame <-> video-frame dataset.
#
# Used by server/rtp_tap.py (live, on the sender), roq_sender/roq_receiver (endpoint
# logs) and tools/build_rtp_dataset.py (offline, on the pcaps of the network device).
#
# H.264 RTP (RFC 6184): one video frame (= one access unit) is carried by one or more
# RTP packets that all share the SAME RTP timestamp; the LAST packet of the frame has
# the marker bit M=1. A packet carries either one whole NAL unit (types 1..23), several
# small NAL units (STAP-A, 24: e.g. SPS+PPS) or a fragment of one big NAL unit (FU-A,
# 28: start bit S on the first fragment, end bit E on the last one). H.265 (RFC 7798)
# is the same idea with AP=48 and FU=49.
'''

import struct

RTP_CLOCK = 90000

H264_NAL = {1: "slice", 5: "IDR", 6: "SEI", 7: "SPS", 8: "PPS", 9: "AUD", 24: "STAP-A", 28: "FU-A"}
H265_NAL = {19: "IDR_W_RADL", 20: "IDR_N_LP", 21: "CRA", 32: "VPS", 33: "SPS", 34: "PPS",
            35: "AUD", 39: "SEI", 48: "AP", 49: "FU"}
SLICE_TYPE = {0: "P", 1: "B", 2: "I", 3: "SP", 4: "SI"}


# --------------------------------------------------------------------------- RTP
def parse_rtp(data):
    """Return dict with the RTP fixed header + payload offset, or None if not RTP v2."""
    if len(data) < 12 or (data[0] >> 6) != 2:
        return None
    b0, b1, seq, ts, ssrc = struct.unpack_from("!BBHII", data, 0)
    cc = b0 & 0x0F
    off = 12 + 4 * cc
    ext_profile, ext_len = None, 0
    if b0 & 0x10:                                  # header extension (e.g. TWCC)
        if len(data) < off + 4:
            return None
        ext_profile, ext_words = struct.unpack_from("!HH", data, off)
        ext_len = 4 + 4 * ext_words
        off += ext_len
    pad = data[-1] if (b0 & 0x20) and len(data) > off else 0
    return {"version": 2, "padding": (b0 >> 5) & 1, "ext": (b0 >> 4) & 1, "cc": cc,
            "marker": (b1 >> 7) & 1, "pt": b1 & 0x7F, "seq": seq, "ts": ts, "ssrc": ssrc,
            "hdr_len": off, "ext_len": ext_len, "ext_profile": ext_profile,
            "payload_len": max(len(data) - off - pad, 0), "pkt_len": len(data), "payload_off": off}


def is_rtcp(data):
    return len(data) >= 4 and (data[0] >> 6) == 2 and 192 <= data[1] <= 223


# --------------------------------------------------------------------------- NAL
class _Bits:
    def __init__(self, b):
        # drop emulation-prevention bytes (00 00 03)
        out = bytearray(); z = 0
        for x in b:
            if z >= 2 and x == 3:
                z = 0; continue
            out.append(x); z = z + 1 if x == 0 else 0
        self.b = bytes(out); self.p = 0

    def bit(self):
        if self.p >= 8 * len(self.b):
            raise EOFError
        v = (self.b[self.p >> 3] >> (7 - (self.p & 7))) & 1; self.p += 1; return v

    def ue(self):
        z = 0
        while self.bit() == 0:
            z += 1
            if z > 31:
                raise EOFError
        v = 0
        for _ in range(z):
            v = (v << 1) | self.bit()
        return (1 << z) - 1 + v


def _h264_slice_type(nal_body):
    """nal_body = bytes after the 1-byte NAL header."""
    try:
        r = _Bits(nal_body[:16]); r.ue()            # first_mb_in_slice
        return SLICE_TYPE.get(r.ue() % 5, "?")
    except EOFError:
        return ""


def parse_nal(payload, codec="h264"):
    """Describe the RTP payload: nal (container type), inner (NAL inside FU/AP),
    fu_start/fu_end, slice (I/P/B for the first fragment / whole slice), keyframe."""
    d = {"nal": "", "inner": "", "fu_start": "", "fu_end": "", "slice": "", "keyframe": 0}
    if not payload:
        return d
    if codec == "h265":
        if len(payload) < 2:
            return d
        t = (payload[0] >> 1) & 0x3F
        d["nal"] = H265_NAL.get(t, str(t))
        if t == 49 and len(payload) >= 3:          # FU
            fu = payload[2]; it = fu & 0x3F
            d.update(inner=H265_NAL.get(it, str(it)), fu_start=fu >> 7, fu_end=(fu >> 6) & 1)
            t = it
        elif t == 48:                              # AP: list aggregated NAL types
            types, o = [], 2
            while o + 2 <= len(payload):
                n = struct.unpack_from("!H", payload, o)[0]; o += 2
                if o < len(payload):
                    types.append(H265_NAL.get((payload[o] >> 1) & 0x3F, "?"))
                o += n
            d["inner"] = "+".join(types)
        d["keyframe"] = int(16 <= t <= 21)
        return d
    t = payload[0] & 0x1F
    d["nal"] = H264_NAL.get(t, str(t))
    if t == 28 and len(payload) >= 2:              # FU-A
        fu = payload[1]; it = fu & 0x1F
        d.update(inner=H264_NAL.get(it, str(it)), fu_start=fu >> 7, fu_end=(fu >> 6) & 1)
        if (fu >> 7) and it in (1, 5):
            d["slice"] = _h264_slice_type(payload[2:])
        t = it
    elif t == 24:                                  # STAP-A
        types, o = [], 1
        while o + 2 <= len(payload):
            n = struct.unpack_from("!H", payload, o)[0]; o += 2
            if o < len(payload):
                types.append(H264_NAL.get(payload[o] & 0x1F, "?"))
            o += n
        d["inner"] = "+".join(types)
    elif t in (1, 5):
        d["slice"] = _h264_slice_type(payload[1:])
    d["keyframe"] = int(t == 5)
    return d


def describe_rtp(data, codec="h264", annexb=False):
    """parse_rtp + parse_nal. annexb=True for RoQ, whose payload is raw Annex-B bytes
    (start codes, not RFC 6184 packetization) behind a 16-byte frag header."""
    r = parse_rtp(data)
    if r is None:
        return None
    pl = data[r["payload_off"]:]
    if annexb:
        r.update(parse_annexb_first(pl[16:], codec))
    else:
        r.update(parse_nal(pl, codec))
    return r


def parse_annexb_first(chunk, codec="h264"):
    """For RoQ fragments: NAL types found after Annex-B start codes in this chunk."""
    d = {"nal": "", "inner": "", "fu_start": "", "fu_end": "", "slice": "", "keyframe": 0}
    types, i = [], 0
    while True:
        j = chunk.find(b"\x00\x00\x01", i)
        if j < 0 or j + 3 >= len(chunk):
            break
        h = chunk[j + 3]
        if codec == "h265":
            t = (h >> 1) & 0x3F; types.append(H265_NAL.get(t, str(t)))
            d["keyframe"] |= int(16 <= t <= 21)
        else:
            t = h & 0x1F; types.append(H264_NAL.get(t, str(t)))
            d["keyframe"] |= int(t == 5)
            if t in (1, 5) and not d["slice"]:
                d["slice"] = _h264_slice_type(chunk[j + 4:])
        i = j + 3
    d["nal"] = "+".join(types) if types else "cont"
    return d


# --------------------------------------------------------------------------- pcap
def read_pcap(path):
    """Yield (ts_float, src_ip, sport, dst_ip, dport, udp_payload, ip_len) for UDP/IPv4.
    Handles Ethernet (1), Linux cooked SLL (113), SLL2 (276) and raw IP (101)."""
    with open(path, "rb") as f:
        gh = f.read(24)
        if len(gh) < 24:
            return
        magic = struct.unpack("<I", gh[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            en = "<"
        elif magic in (0xD4C3B2A1, 0x4D3CB2A1):
            en = ">"
        else:
            raise ValueError(f"{path}: not a pcap file (pcapng? use tcpdump -w)")
        nano = magic in (0xA1B23C4D, 0x4D3CB2A1)
        linktype = struct.unpack(en + "I", gh[20:24])[0]
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                return
            sec, frac, incl, _orig = struct.unpack(en + "IIII", ph)
            pkt = f.read(incl)
            ts = sec + frac / (1e9 if nano else 1e6)
            if linktype == 1:
                off = 14; et = struct.unpack_from("!H", pkt, 12)[0] if len(pkt) >= 14 else 0
                if et == 0x8100 and len(pkt) >= 18:
                    et = struct.unpack_from("!H", pkt, 16)[0]; off = 18
            elif linktype == 113:
                off = 16; et = struct.unpack_from("!H", pkt, 14)[0] if len(pkt) >= 16 else 0
            elif linktype == 276:
                off = 20; et = struct.unpack_from("!H", pkt, 0)[0] if len(pkt) >= 20 else 0
            elif linktype in (101, 12, 228):
                off = 0; et = 0x0800
            else:
                continue
            if et != 0x0800 or len(pkt) < off + 20:
                continue
            ihl = (pkt[off] & 0x0F) * 4
            if pkt[off + 9] != 17:                 # UDP only
                continue
            ip_len = struct.unpack_from("!H", pkt, off + 2)[0]
            src = ".".join(map(str, pkt[off + 12:off + 16]))
            dst = ".".join(map(str, pkt[off + 16:off + 20]))
            u = off + ihl
            if len(pkt) < u + 8:
                continue
            sport, dport, ulen = struct.unpack_from("!HHH", pkt, u)
            yield ts, src, sport, dst, dport, pkt[u + 8:u + ulen], ip_len


# --------------------------------------------------------------------------- logs
# One schema for every vantage point (server tx tap, RoQ endpoints, pcaps).
PKT_COLS = ["vp", "wall", "seq", "rtp_ts", "marker", "pt", "ssrc", "pkt_len", "payload_len",
            "ext", "nal", "inner", "fu_start", "fu_end", "slice", "keyframe", "frame_id", "instance"]


def pkt_row(vp, wall, r, frame_id="", instance=""):
    return ",".join(str(x) for x in (
        vp, f"{wall:.6f}", r["seq"], r["ts"], r["marker"], r["pt"], r["ssrc"], r["pkt_len"],
        r["payload_len"], r["ext"], r.get("nal", ""), r.get("inner", ""), r.get("fu_start", ""),
        r.get("fu_end", ""), r.get("slice", ""), r.get("keyframe", ""), frame_id, instance))
