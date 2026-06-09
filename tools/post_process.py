#!/usr/bin/env python3
"""
CGReplay post-processing — video quality + QoE metrics.

Run from CGReplay/ root:
    python3 tools/post_process.py --mode quic
    python3 tools/post_process.py --mode rtp
    python3 tools/post_process.py --mode scream

Outputs: player/logs/metrics_{mode}.csv
Columns: frame_id, SSIM, PSNR, VMAF, fps, response_time_ms, QoE
Per-frame on a fixed 1..STOP_FRAME-1 axis; un-received frames are NaN (gaps).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import cv2
import pandas as pd
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from video_Quality import compare_images, mask_qr_with_ref, FFMPEG
from qoe import perceived_qoe

CONFIG_PATH   = "config/config.yaml"
REF_FOLDER    = os.path.join("server", "Kombat")
LOGS_DIR      = os.path.join("player", "logs")

with open(CONFIG_PATH) as f:
    cfg = yaml.safe_load(f)

STOP_FRAME  = cfg["Running"]["stop_frm_number"]
FPS_TARGET  = cfg["encoding"]["fps"]


def _pick(*candidates: str) -> str | None:
    """Return the first path that exists (per-mode backup preferred)."""
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def load_fps_rt(mode: str) -> pd.DataFrame:
    """Return DataFrame with frame_id, fps, response_time_ms, timestamp columns.

    Prefers the per-mode log backups written by the topology
    (frame_{mode}.csv, responsetime_{mode}.csv) so a manual re-run or a later
    test doesn't read another mode's shared logs. Falls back to the shared
    files when a backup is absent.

    All three modes use responsetime for command latency, computed identically:
    time from frame receive to joystick command send.
    """
    rt_path = _pick(
        os.path.join(LOGS_DIR, f"responsetime_{mode}.csv"),
        os.path.join(LOGS_DIR, "responsetime_CG.csv"),
    )

    if mode == "quic":
        frame_path = _pick(
            os.path.join(LOGS_DIR, "frame_quic.csv"),
            os.path.join(LOGS_DIR, "ply_quic_frame.csv"),
        )
    else:
        frame_path = _pick(
            os.path.join(LOGS_DIR, f"frame_{mode}.csv"),
            os.path.join(LOGS_DIR, "ply_frame.csv"),
        )

    if frame_path is None:
        # No fps log for this mode (shared file overwritten / not run yet).
        # Return an empty frame so SSIM/PSNR still compute; fps/RT stay NaN.
        print(f"  [warn] no frame log for mode={mode}; fps/RT will be NaN")
        return pd.DataFrame(columns=["frame_id", "fps", "response_time_ms"])

    if mode == "quic":
        df = pd.read_csv(frame_path)[["frame_id", "fps", "recv_time"]]
        df = df.rename(columns={"recv_time": "timestamp"})
    else:
        df = pd.read_csv(frame_path)[["frame_id", "fps"]]

    if rt_path and os.path.exists(rt_path):
        df_rt = pd.read_csv(rt_path)
        df_rt["response_time_ms"] = (
            (df_rt["cmd_timestamp"] - df_rt["frame_timestamp"]) * 1000.0
        )
        merge_cols = ["frame_id", "response_time_ms"]
        if "timestamp" not in df.columns:
            merge_cols += ["frame_timestamp"]
        df = df.merge(df_rt[merge_cols], on="frame_id", how="left")
        if "frame_timestamp" in df.columns:
            df = df.rename(columns={"frame_timestamp": "timestamp"})
    else:
        df["response_time_ms"] = float("nan")

    return df.sort_values("frame_id").reset_index(drop=True)


def compute_vmaf_sequence(tgt_folder: str, frame_ids: list[int]) -> dict:
    """Per-frame VMAF via a single libvmaf pass over the matched sequence.

    Builds two videos (reference + received) from the frames that were actually
    received, QR-masked the same way SSIM/PSNR are, then runs libvmaf once with
    JSON per-frame output. Returns {frame_id: vmaf}. One encode + one libvmaf
    per mode — much faster and more meaningful than per-frame (single-frame
    VMAF has no temporal context and scores far too low).
    """
    frame_ids = sorted(frame_ids)
    if not frame_ids:
        return {}

    ref_dir  = tempfile.mkdtemp(prefix="vmaf_ref_")
    dist_dir = tempfile.mkdtemp(prefix="vmaf_dist_")
    log_path = os.path.join(tempfile.gettempdir(), "vmaf_seq.json")
    index_to_fid = {}
    try:
        seq = 0
        for fid in frame_ids:
            ref  = cv2.imread(os.path.join(REF_FOLDER, f"{fid:04d}.png"))
            dist = cv2.imread(os.path.join(tgt_folder, f"{fid:04d}.png"))
            if ref is None or dist is None:
                continue
            if dist.shape != ref.shape:
                dist = cv2.resize(dist, (ref.shape[1], ref.shape[0]))
            dist_masked = mask_qr_with_ref(ref, dist)
            cv2.imwrite(os.path.join(ref_dir,  f"{seq:04d}.png"), ref)
            cv2.imwrite(os.path.join(dist_dir, f"{seq:04d}.png"), dist_masked)
            index_to_fid[seq] = fid
            seq += 1

        if seq == 0:
            return {}

        ref_mp4  = os.path.join(tempfile.gettempdir(), "vmaf_ref.mp4")
        dist_mp4 = os.path.join(tempfile.gettempdir(), "vmaf_dist.mp4")
        for src, out in [(ref_dir, ref_mp4), (dist_dir, dist_mp4)]:
            subprocess.run(
                [FFMPEG, "-y", "-framerate", str(FPS_TARGET),
                 "-i", os.path.join(src, "%04d.png"),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

        subprocess.run(
            [FFMPEG, "-i", dist_mp4, "-i", ref_mp4, "-lavfi",
             f"[0:v][1:v]libvmaf=log_path={log_path}:log_fmt=json",
             "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        with open(log_path) as f:
            data = json.load(f)
        result = {}
        for fr in data.get("frames", []):
            idx = fr.get("frameNum", fr.get("frame"))
            vmaf = fr.get("metrics", {}).get("vmaf")
            if idx in index_to_fid and vmaf is not None:
                result[index_to_fid[idx]] = vmaf
        return result
    finally:
        shutil.rmtree(ref_dir, ignore_errors=True)
        shutil.rmtree(dist_dir, ignore_errors=True)


def compute_metrics(mode: str):
    print(f"[post_process] mode={mode}")

    quality_csv = os.path.join(LOGS_DIR, f"quality_{mode}.csv")
    metrics_csv = os.path.join(LOGS_DIR, f"metrics_{mode}.csv")

    # Use mode-specific backup if it exists, otherwise fall back to shared dir
    tgt_mode = os.path.join("player", f"received_frames_{mode}")
    tgt_fallback = os.path.join("player", "received_frames")
    tgt_folder = tgt_mode if os.path.isdir(tgt_mode) else tgt_fallback

    # Step 1 — per-frame video quality (SSIM, PSNR)
    print(f"  Computing SSIM/PSNR on frames 1..{STOP_FRAME-1} (frames dir: {tgt_folder}) ...")
    compare_images(
        ref_folder=REF_FOLDER,
        tgt_folder=tgt_folder,
        start_num=1,
        end_num=STOP_FRAME - 1,
        csv_path=quality_csv,
    )

    # Step 2 — load quality and timing data
    df_q = pd.read_csv(quality_csv)
    df_q["frame_id"] = df_q["frame"].str.replace(".png", "", regex=False).astype(int)

    # Step 2b — per-frame VMAF over the received sequence (one libvmaf pass)
    print(f"  Computing VMAF over {len(df_q)} received frames ...")
    vmaf_map = compute_vmaf_sequence(tgt_folder, df_q["frame_id"].tolist())
    df_q["VMAF"] = df_q["frame_id"].map(vmaf_map)

    df_fps = load_fps_rt(mode)

    df = df_q.merge(df_fps, on="frame_id", how="left")

    # Step 3 — perceived quality (QoE) from VMAF + response time.
    # Formula lives in tools/qoe.py (single source of truth, shared with the GUI).
    df["fps"] = df["fps"].fillna(FPS_TARGET)
    df["QoE"] = perceived_qoe(df["VMAF"], df["response_time_ms"])

    # Step 4 — per-frame metrics on a FIXED frame axis (1..STOP_FRAME-1).
    # Every mode shares the same x-axis regardless of how many frames arrived;
    # frames that were never received appear as gaps (NaN), which the plots
    # leave as breaks in the line instead of interpolating across them.
    cols = ["frame_id", "SSIM", "PSNR", "fps", "response_time_ms", "QoE"]
    if "VMAF" in df.columns:
        cols.insert(3, "VMAF")   # keep VMAF next to the other quality metrics
    per_frame = df[cols].sort_values("frame_id")
    full_idx = pd.DataFrame({"frame_id": range(1, STOP_FRAME)})
    out = full_idx.merge(per_frame, on="frame_id", how="left")

    out.to_csv(metrics_csv, index=False)
    received = int(out["SSIM"].notna().sum())
    print(f"  Saved: {metrics_csv}  ({len(out)} frames, {received} received)")
    print(f"  Avg SSIM={out['SSIM'].mean():.4f}  "
          f"Avg RT={out['response_time_ms'].mean():.1f}ms  "
          f"Avg QoE={out['QoE'].mean():.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["quic", "rtp", "scream"], required=True)
    args = parser.parse_args()
    compute_metrics(args.mode)
