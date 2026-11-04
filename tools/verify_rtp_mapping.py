#!/usr/bin/env python3
"""
CGReplay++ — independent check of the RTP-frame -> video-frame mapping of a run.

Re-assembles every H.264/H.265 access unit (all RTP packets with the same RTP timestamp,
RFC 6184 / RFC 7798 depacketization) from a pcap, decodes it, reads the QR code the
server burned into the picture, and compares that frame id with the frame id
tools/build_rtp_dataset.py assigned to that RTP timestamp.

    python3 tools/verify_rtp_mapping.py results/<run_id> [--pcap s1-eth1] [--max 400]

Needs PyAV (pip install av) and pyzbar. Not for RoQ (its RTP is inside encrypted QUIC;
RoQ frames carry their frame id in the frag header, so there is nothing to verify).
"""
import argparse
import os
import re
import sys

import av
import cv2
import pandas as pd
from pyzbar import pyzbar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rtp_parse import parse_rtp, read_pcap   # noqa: E402

SC = b"\x00\x00\x00\x01"


def depay(payload, codec):
    if codec == "h265":
        t = (payload[0] >> 1) & 0x3F
        if t == 49:                                       # FU
            fu = payload[2]
            hdr = bytes([(payload[0] & 0x81) | ((fu & 0x3F) << 1), payload[1]])
            return (SC + hdr if fu & 0x80 else b"") + payload[3:]
        if t == 48:                                       # AP
            out, o = b"", 2
            while o + 2 <= len(payload):
                n = int.from_bytes(payload[o:o + 2], "big"); o += 2
                out += SC + payload[o:o + n]; o += n
            return out
        return SC + payload
    t = payload[0] & 0x1F
    if t == 28:                                           # FU-A
        fu = payload[1]
        return (SC + bytes([(payload[0] & 0xE0) | (fu & 0x1F)]) if fu & 0x80 else b"") + payload[2:]
    if t == 24:                                           # STAP-A
        out, o = b"", 1
        while o + 2 <= len(payload):
            n = int.from_bytes(payload[o:o + 2], "big"); o += 2
            out += SC + payload[o:o + n]; o += n
        return out
    return SC + payload


def qr_id(img):
    h, w = img.shape[:2]
    for q in pyzbar.decode(img[h - 260:h, w - 260:w]):
        m = re.search(r"Frame ID:\s*(\d+)", q.data.decode("utf-8", "ignore"))
        if m:
            return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--pcap", default="s1-eth1", help="vantage point pcap (default s1-eth1)")
    ap.add_argument("--max", type=int, default=100000)
    a = ap.parse_args()
    fr = pd.read_csv(os.path.join(a.run, "dataset", "rtp_packets.csv"))
    codec = "h264"
    if os.path.exists(os.path.join(a.run, "run.json")):
        import json
        codec = json.load(open(os.path.join(a.run, "run.json"))).get("codec", "h264")
    srv = fr[fr["vp"] == "server_tx"]
    ssrc = int(srv["ssrc"].mode().iloc[0])
    ts2fid = srv.dropna(subset=["frame_id"]).groupby("rtp_ts")["frame_id"].first().astype(int).to_dict()

    aus, order = {}, []
    for ts, src, sp, dst, dp, pl, _ in read_pcap(os.path.join(a.run, "pcap", f"{a.pcap}.pcap")):
        r = parse_rtp(pl)
        if r is None or r["ssrc"] != ssrc or r["pt"] < 96:
            continue
        if r["ts"] not in aus:
            aus[r["ts"]] = []; order.append(r["ts"])
        aus[r["ts"]].append((r["seq"], pl[r["payload_off"]:]))
    dec = av.CodecContext.create("hevc" if codec == "h265" else "h264", "r")
    ok = bad = noqr = undecoded = 0
    examples = []
    for ts in order[:a.max]:
        pk = sorted(aus[ts], key=lambda x: x[0] if x[0] >= aus[ts][0][0] - 30000 else x[0] + 65536)
        data = b"".join(depay(p, codec) for _, p in pk if p)
        try:
            frames = dec.decode(av.Packet(data))
        except av.error.InvalidDataError:
            frames = []
        if not frames:
            undecoded += 1; continue
        got = qr_id(frames[-1].to_ndarray(format="bgr24"))
        exp = ts2fid.get(ts)
        if got is None or exp is None:
            noqr += 1
        elif got == exp:
            ok += 1
        else:
            bad += 1
            if len(examples) < 5:
                examples.append((ts, exp, got))
    total = ok + bad
    print(f"{a.run} [{a.pcap}]: RTP frames={len(order)}  checked={total}  match={ok}  "
          f"MISMATCH={bad}  no-QR/unmapped={noqr}  undecodable={undecoded}  "
          f"accuracy={ok / total:.2%}" if total else "nothing checked")
    for e in examples:
        print(f"   rtp_ts={e[0]} mapped frame_id={e[1]} but picture shows {e[2]}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
