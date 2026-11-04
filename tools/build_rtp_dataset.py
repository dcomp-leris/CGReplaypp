#!/usr/bin/env python3
"""
CGReplay++ — build the RTP-frame <-> video-frame dataset of one or more runs.

    python3 tools/build_rtp_dataset.py results/<run_id> [results/<run_id2> ...] \
            [--combined results/all_runs.csv] [--no-vmaf] [--no-lpips] [--ref server/Kombat]

A run folder is what topology/simple_topology.py collects (results/<run_id>/):
    run.json, config.yaml, server_logs/, player_logs/, pcap/{s1-eth1,s1-eth2,h2}.pcap,
    received_frames/

Vantage points (vp)
    server_tx  RTP as it leaves the server (server/rtp_tap.py, or roq_sender for RoQ)
    s1-eth1    network device: switch port facing the server
    s1-eth2    network device: switch port facing the player
    h2         player NIC
    player_rx  RTP as the RoQ receiver sees it (RoQ hides RTP inside encrypted QUIC,
               so for RoQ the pcaps only give QUIC datagram timing/sizes)

Output (results/<run_id>/dataset/)
    rtp_packets.csv        one row per RTP packet per vp
    rtp_frames.csv         one row per RTP frame (= packets sharing one RTP timestamp,
                           last one has marker=1) per vp, with loss/reorder/delay/jitter
    video_frames.csv       THE dataset: one row per video frame the server sent, joining
                           the server frame, its RTP frame at every vp, and the decoded
                           frame at the player with SSIM / PSNR / VMAF / LPIPS
    commands.csv           player->server messages (Ack/Nack/command) seen per vp
    other_packets.csv      non-RTP UDP per vp (RTCP feedback, QUIC datagrams, ...)
"""

import argparse
import bisect
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rtp_parse import PKT_COLS, RTP_CLOCK, describe_rtp, is_rtcp, read_pcap   # noqa: E402

VP_ORDER = ["server_tx", "s1-eth1", "s1-eth2", "h2", "player_rx"]
QR_RE = re.compile(r"Frame ID:\s*(\d+)(?:.*?rcv_timestamp:\s*([0-9.eE+-]+))?")


# ============================================================================ packets
def roq_rtp_from_pcap(pcap, keylog, codec, vp):
    """RoQ: decrypt the QUIC pcap with the receiver's TLS key log (tshark) and return the
    RTP header of every DATAGRAM frame. Without the key log RTP is not visible on the wire."""
    tshark, editcap = shutil.which("tshark"), shutil.which("editcap")
    if not (tshark and editcap and keylog and os.path.exists(keylog)):
        return []
    tmp = tempfile.mktemp(suffix=".pcapng")
    try:
        subprocess.run([editcap, "--inject-secrets", f"tls,{keylog}", pcap, tmp], check=True,
                       capture_output=True)
        out = subprocess.run([tshark, "-r", tmp, "-d", "udp.port==4433,quic", "-Y", "quic.dg",
                              "-T", "fields", "-e", "frame.time_epoch", "-e", "ip.src", "-e", "quic.dg"],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"  [WARN] RoQ decryption of {pcap} failed: {e}")
        return []
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        for h in parts[2].split(","):
            b = bytes.fromhex(h.replace(":", ""))
            r = describe_rtp(b[:12 + 16 + 48], codec, annexb=True)
            if r is None:
                continue
            fid = int.from_bytes(b[12:16], "big") if len(b) >= 16 else ""
            rows.append([vp, float(parts[0]), r["seq"], r["ts"], r["marker"], r["pt"], r["ssrc"], len(b),
                         len(b) - 12, r["ext"], r["nal"], r["inner"], r["fu_start"], r["fu_end"],
                         r["slice"], r["keyframe"], fid, fid])
    return rows


def load_packets(run, cfg, codec):
    srv_ip, ply_ip = cfg["server"]["server_IP"], cfg["gamer"]["player_IP"]
    rtp_port = cfg["gamer"]["player_streaming_port"]
    cmd_port = cfg["gamer"]["player_command_port"]
    frames = []
    for name, vp in (("server_logs/srv_rtp.csv", "server_tx"), ("player_logs/ply_rtp.csv", "player_rx")):
        p = os.path.join(run, name)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            d = pd.read_csv(p)
            if len(d):
                d["vp"] = vp
                frames.append(d)
    srv_ssrcs = set()
    for d in frames:
        if d["vp"].iloc[0] == "server_tx":
            srv_ssrcs = set(d["ssrc"].astype("int64").unique())
    cmds, other = [], []
    for pcap in sorted(glob.glob(os.path.join(run, "pcap", "*.pcap"))):
        vp = os.path.splitext(os.path.basename(pcap))[0]
        rows = []
        try:
            for ts, src, sport, dst, dport, pl, ip_len in read_pcap(pcap):
                if not is_rtcp(pl) and (dport == rtp_port or srv_ssrcs) and src != ply_ip:
                    r = describe_rtp(pl, codec)
                    # video = RTP from the server's SSRC (or to the RTP port when no server log)
                    if r is not None and r["pt"] >= 96 and (r["ssrc"] in srv_ssrcs if srv_ssrcs else dport == rtp_port):
                        rows.append([vp, ts, r["seq"], r["ts"], r["marker"], r["pt"], r["ssrc"],
                                     r["pkt_len"], r["payload_len"], r["ext"], r["nal"], r["inner"],
                                     r["fu_start"], r["fu_end"], r["slice"], r["keyframe"], "", ""])
                        continue
                if dport == cmd_port and src == ply_ip:
                    txt = pl.decode(errors="replace").strip()
                    try:
                        head, fid, typ, num, fps, cps = txt.rsplit(",", 5)
                        cmds.append([vp, ts, typ, int(fid), num, fps, cps, len(pl)])
                    except ValueError:
                        cmds.append([vp, ts, "?", "", "", "", "", len(pl)])
                    continue
                kind = "rtcp" if is_rtcp(pl) else ("quic" if 4433 in (sport, dport) else "udp")
                rtcp_pt = pl[1] if kind == "rtcp" else ""
                rtcp_fmt = (pl[0] & 0x1F) if kind == "rtcp" else ""
                other.append([vp, ts, src, sport, dst, dport, kind, rtcp_pt, rtcp_fmt, len(pl), ip_len])
        except (ValueError, struct_error) as e:
            print(f"  [WARN] {pcap}: {e}")
        if not rows:     # RoQ: RTP is inside encrypted QUIC -> decrypt with the key log if present
            rows = roq_rtp_from_pcap(pcap, os.path.join(run, "player_logs", "quic_keylog.txt"), codec, vp)
            if rows:
                print(f"  {vp}: RoQ pcap decrypted with quic_keylog.txt -> {len(rows)} RTP datagrams")
        if rows:
            frames.append(pd.DataFrame(rows, columns=PKT_COLS))
    pk = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PKT_COLS)
    cm = pd.DataFrame(cmds, columns=["vp", "wall", "type", "frame_id", "number", "fps", "cps", "size"])
    ot = pd.DataFrame(other, columns=["vp", "wall", "src", "sport", "dst", "dport", "kind",
                                      "rtcp_pt", "rtcp_fmt", "udp_payload", "ip_len"])
    return pk, cm, ot


try:
    from struct import error as struct_error
except ImportError:      # pragma: no cover
    struct_error = Exception


def per_packet_features(pk):
    """Arrival-order features per vp: extended seq, reordered flag, duplicate flag,
    RFC 3550 interarrival jitter (ms)."""
    out = []
    for vp, d in pk.groupby("vp", sort=False):
        d = d.sort_values("wall", kind="stable").copy()
        ext, hi, reo, dup, seen = [], None, [], [], set()
        for s in d["seq"].astype(int):
            e = s if hi is None else ((hi & ~0xFFFF) | s)
            if hi is not None:
                if e < hi - 0x8000:
                    e += 0x10000
                elif e > hi + 0x8000:
                    e -= 0x10000
            reo.append(int(hi is not None and e < hi))
            dup.append(int(e in seen)); seen.add(e)
            ext.append(e); hi = e if hi is None else max(hi, e)
        d["ext_seq"] = ext; d["reordered"] = reo; d["duplicate"] = dup
        transit = d["wall"].values - d["rtp_ts"].astype("int64").values / RTP_CLOCK
        j, js = 0.0, []
        for i in range(len(transit)):
            if i:
                j += (abs(transit[i] - transit[i - 1]) - j) / 16.0
            js.append(j * 1000.0)
        d["jitter_ms"] = js
        out.append(d)
    return pd.concat(out, ignore_index=True) if out else pk


# ============================================================================ frames
def rtp_frames(pk, srv_frames):
    """Group packets into RTP frames per vp and compute frame-level network features."""
    video_ssrc = pk[pk["vp"] == "server_tx"]["ssrc"].mode()
    if len(video_ssrc):
        pk = pk[pk["ssrc"] == video_ssrc.iloc[0]]
    srv = pk[pk["vp"] == "server_tx"]
    srv_seqs = srv.groupby("rtp_ts")["seq"].apply(lambda s: set(s.astype(int))).to_dict()
    srv_time = srv.groupby("rtp_ts")["wall"].agg(["min", "max"])
    ts2inst = srv_frames.set_index("rtp_ts")[["instance", "frame_id"]].to_dict("index") if len(srv_frames) else {}

    rows = []
    for (vp, ts), g in pk.groupby(["vp", "rtp_ts"], sort=False):
        seqs = set(g["seq"].astype(int))
        exp = srv_seqs.get(ts)
        n_exp = len(exp) if exp else (int(g["seq"].max()) - int(g["seq"].min()) + 1)
        lost = len(exp - seqs) if exp else max(n_exp - len(seqs), 0)
        slices = [s for s in g["slice"].astype(str) if s in ("I", "P", "B", "SI", "SP")]
        nals = sorted(set(x for x in (g["nal"].astype(str).tolist() + g["inner"].astype(str).tolist())
                          if x and x != "nan"))
        t0, t1 = g["wall"].min(), g["wall"].max()
        mk = g[g["marker"].astype(int) == 1]
        info = ts2inst.get(ts, {})
        rows.append({
            "vp": vp, "rtp_ts": int(ts), "instance": info.get("instance", ""),
            "frame_id": info.get("frame_id", ""),
            "pkts": len(g), "pkts_expected": n_exp, "lost_pkts": lost,
            "complete": int(lost == 0 and len(mk) > 0), "marker_seen": int(len(mk) > 0),
            "reordered_pkts": int(g["reordered"].sum()), "dup_pkts": int(g["duplicate"].sum()),
            "rtp_bytes": int(g["pkt_len"].sum()), "payload_bytes": int(g["payload_len"].sum()),
            "first_wall": t0, "last_wall": t1, "marker_wall": mk["wall"].min() if len(mk) else np.nan,
            "spread_ms": (t1 - t0) * 1000.0,
            "owd_first_ms": (t0 - srv_time.loc[ts, "min"]) * 1000.0 if ts in srv_time.index and vp != "server_tx" else np.nan,
            "owd_last_ms": (t1 - srv_time.loc[ts, "max"]) * 1000.0 if ts in srv_time.index and vp != "server_tx" else np.nan,
            "jitter_ms": g["jitter_ms"].iloc[-1],
            "frame_type": slices[0] if slices else "", "keyframe": int(g["keyframe"].astype(float).max() > 0),
            "nal_types": "+".join(nals), "first_seq": int(g["ext_seq"].min()), "last_seq": int(g["ext_seq"].max()),
        })
    fr = pd.DataFrame(rows)
    if fr.empty:
        return fr
    out = []
    for vp, d in fr.groupby("vp", sort=False):
        d = d.sort_values("first_seq").copy()
        dts = d["rtp_ts"].diff().mod(1 << 32)
        d["rtp_ts_delta"] = dts
        d["fps_rtp_ts"] = RTP_CLOCK / dts.replace(0, np.nan)              # server frame rate (RTP header)
        arr = d["last_wall"]
        d["iat_ms"] = arr.diff() * 1000.0                                  # frame inter-arrival at this vp
        d["fps_arrival"] = 1000.0 / d["iat_ms"].replace(0, np.nan)
        # throughput over the last 1 s at this vp
        t = d["last_wall"].values; b = d["rtp_bytes"].values.cumsum()
        k = np.searchsorted(t, t - 1.0, side="left")
        prev = np.where(k > 0, b[np.maximum(k - 1, 0)], 0)
        d["kbps_1s"] = (b - prev) * 8 / 1000.0
        out.append(d)
    return pd.concat(out, ignore_index=True)


# ============================================================================ player
def decode_qr(path):
    from pyzbar import pyzbar
    img = cv2.imread(path)
    if img is None:
        return None, None
    h, w = img.shape[:2]
    crop = img[max(h - 260, 0):h, max(w - 260, 0):w]          # QR sits bottom-right
    for im in (crop, img):
        for blur in (False, True):
            src = cv2.GaussianBlur(im, (5, 5), 0) if blur else im
            for q in pyzbar.decode(src):
                m = QR_RE.search(q.data.decode("utf-8", "ignore"))
                if m:
                    return int(m.group(1)), (float(m.group(2)) if m.group(2) else None)
    return None, None


def map_player(run, srv_frames, protocol):
    p = os.path.join(run, "player_logs", "ply_video_frames.csv")
    if not os.path.exists(p):
        return pd.DataFrame()
    pf = pd.read_csv(p)
    pf["png"] = pf["saved_png"].fillna("").astype(str).map(
        lambda s: os.path.join(run, "received_frames", os.path.basename(s)) if s else "")
    pf["png"] = pf["png"].map(lambda s: s if s and os.path.exists(s) else "")
    sf = srv_frames.sort_values("instance").reset_index(drop=True)
    order = {int(r.instance): i for i, r in sf.iterrows()}
    inst = []
    for r in pf.itertuples():
        if protocol == "roq" and not pd.isna(r.frame_id) and str(r.frame_id) != "":
            m = sf[sf["frame_id"] == int(r.frame_id)]
            inst.append((int(m["instance"].iloc[0]), "frame_header") if len(m) else (None, "none"))
            continue
        if not r.png:
            inst.append((None, "none")); continue
        fid, qts = decode_qr(r.png)
        if fid is None:
            inst.append((None, "none")); continue
        m = sf[sf["frame_id"] == fid]
        if qts is not None and len(m):
            m2 = m[(m["qr_ts"].astype(float) - qts).abs() < 1e-5]
            m = m2 if len(m2) else m
        inst.append((int(m["instance"].iloc[0]), "qr") if len(m) else (None, "none"))
    pf["instance"] = [i for i, _ in inst]; pf["map_method"] = [m for _, m in inst]
    # fill gaps between two QR anchors when decode count == server-frame count between them
    anchors = [(i, order[int(x)]) for i, x in enumerate(pf["instance"]) if x is not None and not pd.isna(x)]
    for (i0, o0), (i1, o1) in zip(anchors, anchors[1:]):
        if i1 - i0 == o1 - o0 and i1 - i0 > 1:
            for k in range(1, i1 - i0):
                if pd.isna(pf.at[i0 + k, "instance"]) or pf.at[i0 + k, "instance"] is None:
                    pf.at[i0 + k, "instance"] = int(sf.at[o0 + k, "instance"])
                    pf.at[i0 + k, "map_method"] = "interp"
    pf["player_fps"] = 1.0 / pf["recv_wall"].diff().replace(0, np.nan)
    return pf


# ============================================================================ quality
def reference(ref_dir, frame_id, w, h):
    img = cv2.imread(os.path.join(ref_dir, f"{int(frame_id):04d}.png"))
    if img is None:
        return None
    if img.shape[1] != w or img.shape[0] != h:     # same resize the server applies
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return img


def mask_qr(ref, dist, qr=200, pad=10):
    h, w = ref.shape[:2]
    d = dist.copy()
    d[h - qr - pad:h - pad, w - qr - pad:w - pad] = ref[h - qr - pad:h - pad, w - qr - pad:w - pad]
    return d


def find_ffmpeg():
    for c in (os.environ.get("CGREPLAY_FFMPEG"), "/home/hugo/CGSynth/tools/ffmpeg-vmaf/ffmpeg", "ffmpeg"):
        if not c:
            continue
        try:
            o = subprocess.run([c, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
            if "libvmaf" in o:
                return c
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def quality(pairs, w, h, fps, want_vmaf, want_lpips):
    """pairs: list of (key, ref_img, dist_img) in decode order -> {key: metrics}."""
    from skimage.metrics import structural_similarity
    res = {}
    for key, ref, dist in pairs:
        g1 = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY); g2 = cv2.cvtColor(dist, cv2.COLOR_BGR2GRAY)
        res[key] = {"PSNR": cv2.PSNR(ref, dist), "SSIM": structural_similarity(g1, g2, data_range=255)}
    if want_lpips and pairs:
        try:
            import lpips, torch
            net = lpips.LPIPS(net="alex", verbose=False)
            with torch.no_grad():
                for key, ref, dist in pairs:
                    t = [torch.from_numpy(cv2.cvtColor(x, cv2.COLOR_BGR2RGB)).permute(2, 0, 1)[None].float() / 127.5 - 1
                         for x in (ref, dist)]
                    res[key]["LPIPS"] = float(net(t[0], t[1]).item())
        except Exception as e:             # ImportError, or a broken torch/CUDA install
            print(f"  [INFO] LPIPS skipped ({type(e).__name__}: {str(e)[:80]}; pip install lpips torch)")
    if want_vmaf and pairs:
        ff = find_ffmpeg()
        if ff is None:
            print("  [INFO] VMAF skipped (no ffmpeg with libvmaf; set CGREPLAY_FFMPEG)")
            return res
        work = tempfile.mkdtemp(prefix="cg_vmaf_")
        try:
            for i, (key, ref, dist) in enumerate(pairs):
                cv2.imwrite(os.path.join(work, f"r{i:05d}.png"), ref)
                cv2.imwrite(os.path.join(work, f"d{i:05d}.png"), dist)
            log = os.path.join(work, "vmaf.json")
            subprocess.run([ff, "-hide_banner", "-loglevel", "error",
                            "-framerate", str(fps), "-i", os.path.join(work, "d%05d.png"),
                            "-framerate", str(fps), "-i", os.path.join(work, "r%05d.png"),
                            "-lavfi", f"[0:v]format=yuv420p[d];[1:v]format=yuv420p[r];"
                                      f"[d][r]libvmaf=log_fmt=json:log_path={log}:n_threads=4",
                            "-f", "null", "-"], check=False)
            if os.path.exists(log):
                for fr in json.load(open(log)).get("frames", []):
                    i = fr.get("frameNum", fr.get("frame"))
                    if i is not None and i < len(pairs):
                        res[pairs[i][0]]["VMAF"] = fr["metrics"].get("vmaf")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return res


# ============================================================================ main
def build(run, args):
    meta = json.load(open(os.path.join(run, "run.json"))) if os.path.exists(os.path.join(run, "run.json")) else {}
    cfg_path = os.path.join(run, "config.yaml") if os.path.exists(os.path.join(run, "config.yaml")) else "config/config.yaml"
    cfg = yaml.safe_load(open(cfg_path))
    game = meta.get("game", cfg["Running"]["game"])
    protocol = meta.get("protocol", "rtp")
    codec = meta.get("codec") or ("h265" if cfg["encoding"]["name"].upper() in ("H.265", "H265", "HEVC") else "h264")
    w, h = cfg["encoding"]["resolution"]["width"], cfg["encoding"]["resolution"]["height"]
    fps = cfg["encoding"]["fps"]
    ref_dir = args.ref or meta.get("reference_frames") or os.path.join("server", cfg[game]["frames"].lstrip("./"))
    out = os.path.join(run, "dataset"); os.makedirs(out, exist_ok=True)
    print(f"== {run}  game={game} transport={meta.get('transport', protocol)} codec={codec}")

    pk, cm, ot = load_packets(run, cfg, codec)
    sfp = os.path.join(run, "server_logs", "srv_video_frames.csv")
    srv_frames = pd.read_csv(sfp) if os.path.exists(sfp) else pd.DataFrame(
        columns=["instance", "frame_id", "qr_ts", "push_wall", "rtp_ts"])
    srv_frames = srv_frames.dropna(subset=["rtp_ts"]).copy()
    srv_frames["rtp_ts"] = srv_frames["rtp_ts"].astype("int64")
    if len(pk):
        pk = per_packet_features(pk)
        ts2 = srv_frames.set_index("rtp_ts")[["frame_id", "instance"]]
        pk["frame_id"] = pk["rtp_ts"].map(ts2["frame_id"]); pk["instance"] = pk["rtp_ts"].map(ts2["instance"])
    pk.to_csv(os.path.join(out, "rtp_packets.csv"), index=False)
    cm.to_csv(os.path.join(out, "commands.csv"), index=False)
    ot.to_csv(os.path.join(out, "other_packets.csv"), index=False)
    print(f"  packets: " + ", ".join(f"{vp}={n}" for vp, n in pk["vp"].value_counts().items()))

    fr = rtp_frames(pk, srv_frames) if len(pk) else pd.DataFrame()
    fr.to_csv(os.path.join(out, "rtp_frames.csv"), index=False)

    # ---- one row per server video frame -------------------------------------------------
    vf = srv_frames.sort_values("instance").copy()
    vf["server_fps_push"] = 1.0 / vf["push_wall"].diff().replace(0, np.nan)
    if len(fr):
        feats = ["pkts", "pkts_expected", "lost_pkts", "complete", "reordered_pkts", "dup_pkts",
                 "rtp_bytes", "payload_bytes", "first_wall", "last_wall", "spread_ms", "owd_first_ms",
                 "owd_last_ms", "jitter_ms", "iat_ms", "fps_arrival", "fps_rtp_ts", "kbps_1s"]
        st = fr[fr["vp"] == "server_tx"].set_index("rtp_ts")
        for c in ("frame_type", "keyframe", "nal_types", "rtp_ts_delta", "fps_rtp_ts"):
            if c in st:
                vf[c if c != "fps_rtp_ts" else "server_fps_rtp"] = vf["rtp_ts"].map(st[c])
        for vp in [v for v in VP_ORDER if v in set(fr["vp"])] + sorted(set(fr["vp"]) - set(VP_ORDER)):
            d = fr[fr["vp"] == vp].drop_duplicates("rtp_ts").set_index("rtp_ts")
            for c in feats:
                if vp == "server_tx" and c in ("owd_first_ms", "owd_last_ms", "lost_pkts", "complete"):
                    continue
                vf[f"{vp}_{c}"] = vf["rtp_ts"].map(d[c])
            if vp != "server_tx":
                vf[f"{vp}_received"] = vf[f"{vp}_pkts"].notna().astype(int)

    pf = map_player(run, srv_frames, protocol)
    if len(pf):
        m = pf.dropna(subset=["instance"]).copy()
        m["instance"] = m["instance"].astype(int)
        m = m.drop_duplicates("instance", keep="first").set_index("instance")
        vf["player_decoded"] = vf["instance"].isin(m.index).astype(int)
        vf["player_decode_idx"] = vf["instance"].map(m["decode_idx"])
        vf["player_recv_wall"] = vf["instance"].map(m["recv_wall"])
        vf["player_fps"] = vf["instance"].map(m["player_fps"])
        vf["player_map_method"] = vf["instance"].map(m["map_method"])
        vf["player_png"] = vf["instance"].map(m["png"])
        vf["e2e_latency_ms"] = (vf["player_recv_wall"] - vf["push_wall"]) * 1000.0
        pf.to_csv(os.path.join(out, "player_frames.csv"), index=False)
        print(f"  player: decoded={len(pf)}  mapped: " +
              ", ".join(f"{k}={v}" for k, v in pf["map_method"].value_counts().items()))

        pairs = []
        for r in vf.dropna(subset=["player_png"]).sort_values("player_decode_idx").itertuples():
            if not r.player_png:
                continue
            ref = reference(ref_dir, r.frame_id, w, h); dist = cv2.imread(r.player_png)
            if ref is None or dist is None:
                continue
            if dist.shape != ref.shape:
                dist = cv2.resize(dist, (ref.shape[1], ref.shape[0]))
            pairs.append((int(r.instance), ref, mask_qr(ref, dist)))
        q = quality(pairs, w, h, fps, not args.no_vmaf, not args.no_lpips)
        for metric in ("PSNR", "SSIM", "VMAF", "LPIPS"):
            vals = {k: v.get(metric) for k, v in q.items() if v.get(metric) is not None}
            if vals:
                vf[metric] = vf["instance"].map(vals)
        print(f"  quality computed for {len(pairs)} frames ({', '.join(c for c in ('PSNR','SSIM','VMAF','LPIPS') if c in vf)})")
    for k in ("game", "transport", "codec", "bw_mbps", "bottleneck_mbps", "delay", "loss_pct"):
        vf.insert(0, k, meta.get(k, ""))
    vf.insert(0, "run_id", meta.get("run_id", os.path.basename(os.path.normpath(run))))
    vf.to_csv(os.path.join(out, "video_frames.csv"), index=False)
    print(f"  -> {out}/video_frames.csv  ({len(vf)} server frames, {vf.shape[1]} columns)")
    return vf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="results/<run_id> folders")
    ap.add_argument("--combined", help="also write all runs' video_frames into one CSV")
    ap.add_argument("--ref", help="reference frame folder (default: from run.json / config)")
    ap.add_argument("--no-vmaf", action="store_true")
    ap.add_argument("--no-lpips", action="store_true")
    args = ap.parse_args()
    runs = [r for r in args.runs if os.path.isdir(r)]   # tolerate shell globs that catch files
    allv = [build(r, args) for r in runs]
    if args.combined:
        pd.concat([v for v in allv if len(v)], ignore_index=True, sort=False).to_csv(args.combined, index=False)
        print(f"combined -> {args.combined}")


if __name__ == "__main__":
    main()
