#!/usr/bin/env python3
"""
CGReplay QUIC Receiver — player side.

Connects to quic_sender.py on the server.
Receives one video frame per QUIC stream (server-unidirectional).
Sends Ack / Nack / command on a client-initiated unidirectional stream.

Run from CGReplay/player/:
    source ~/venv/bin/activate
    python3 quic_receiver.py
"""

import asyncio
import os
import struct
import time
import subprocess
import glob
import yaml
import cv2
import numpy as np
import pandas as pd
import av
from collections import defaultdict
from pyzbar import pyzbar
from aioquic.asyncio import connect
from aioquic.quic.configuration import QuicConfiguration
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.events import (
    StreamDataReceived,
    HandshakeCompleted,
    ConnectionTerminated,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = "../config/config.yaml"

with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

GAME            = config["Running"]["game"]
STOP_FRAME      = config["Running"]["stop_frm_number"]
SERVER_IP       = config["server"]["server_IP"]
QUIC_PORT       = config["protocols"].get("quic_port", 4433)
PLAYER_IP       = config["gamer"]["player_IP"]
ACK_FREQ        = config["sync"]["ack_freq"]
LIVE_WATCHING   = config["Running"]["live_watching"]
SYNC_FILE       = config[GAME]["sync_file"]
ENC_NAME        = config["encoding"]["name"]                 # "H.264" or "H.265"
DEC_CODEC       = "hevc" if ENC_NAME.strip().upper() in ("H.265", "H265", "HEVC") else "h264"

LOG_DIR = "./logs"
RECV_DIR = "./received_frames"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RECV_DIR, exist_ok=True)

# Clean previous received frames
for f in glob.glob(os.path.join(RECV_DIR, "*.png")):
    os.remove(f)

# Frame header sent by quic_sender: [4B frame_id][4B payload_size][8B send_timestamp]
HEADER_FMT  = "!IId"
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 16 bytes

FRAME_LOG = os.path.join(LOG_DIR, "ply_quic_frame.csv")
RATE_LOG  = os.path.join(LOG_DIR, "ratelog_quic.csv")
EVENT_LOG = os.path.join(LOG_DIR, "ply_quic_events.csv")
RT_LOG    = os.path.join(LOG_DIR, "responsetime_CG.csv")

with open(FRAME_LOG, "w") as f:
    f.write("frame_id,size_bytes,recv_time,fps\n")
with open(RATE_LOG, "w") as f:
    f.write("frame_id,fps,cps\n")
with open(EVENT_LOG, "w") as f:
    f.write("timestamp,event,frame_id,size_bytes,fps,cmd_count,response_time_ms\n")
with open(RT_LOG, "w") as f:
    f.write("frame_id,frame_timestamp,cmd_timestamp\n")

# ---------------------------------------------------------------------------
# Sync file loader — same format as cg_gamer1.py
# ---------------------------------------------------------------------------

def load_syncfile(file_path: str) -> pd.DataFrame:
    rows = []
    with open(file_path) as fh:
        next(fh)  # skip header
        for line in fh:
            parts = line.rsplit(",", 1)
            if len(parts) == 2:
                id_cmd, enc = parts
                id_str, cmd_str = id_cmd.split(",", 1)
                rows.append((int(id_str), cmd_str, enc.strip()))
    return pd.DataFrame(rows, columns=["ID", "command", "encrypted_cmd"])

sync_df = load_syncfile(SYNC_FILE)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def decode_h264(data: bytes) -> np.ndarray | None:
    """Decode a single I-frame (H.264 or H.265, independent — no prior context)."""
    try:
        codec = av.CodecContext.create(DEC_CODEC, 'r')
        frames = codec.decode(av.Packet(data))
        if frames:
            return frames[0].to_ndarray(format='bgr24')
    except Exception as e:
        print(f"[WARN] {DEC_CODEC} decode error: {e}")
    return None


def read_qr(frame: np.ndarray) -> tuple[int, str | None]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    codes = pyzbar.decode(blurred)
    for qr in codes:
        data = qr.data.decode()
        for part in data.split(","):
            if "ID:" in part:
                try:
                    return int(part.split(":")[1].strip()), data
                except ValueError:
                    pass
    return -1, None


# ---------------------------------------------------------------------------
# Live display — handled by a separate process (frame_viewer.py) running under
# the SYSTEM Python. The venv's Qt5 OpenCV does not render reliably as root
# inside Mininet; the system's GTK build does (same path RTP/SCReAM use). The
# receiver just writes frames to RECV_DIR and the viewer shows the newest one.
# ---------------------------------------------------------------------------
SYS_PY      = "/usr/bin/python3"
VIEWER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frame_viewer.py")
STOP_FILE   = "/tmp/quic_viewer_stop"


# ---------------------------------------------------------------------------
# QUIC protocol handler
# ---------------------------------------------------------------------------

class QUICReceiverProtocol(QuicConnectionProtocol):
    """
    Accumulates per-stream data until end_stream, then decodes the frame.
    Sends Ack to server on a client-initiated unidirectional stream.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Buffer per stream: stream_id -> bytearray
        self._buffers: dict[int, bytearray] = defaultdict(bytearray)
        self._ctrl_stream_id: int | None = None
        self._frame_count = 0
        self._cmd_count = 0
        self._frame_counter = 1        # tracks sequence for sync file matching
        self._previous_time = time.perf_counter()
        self._cmd_previous_time = time.perf_counter()
        self._current_fps = 0.0
        self._current_cps = 0.0
        self._session_start = time.perf_counter()
        self._done = asyncio.Event()

    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            print("[QUIC] Handshake complete — waiting for frames")
            # Open the control stream now (client-unidirectional)
            self._ctrl_stream_id = self._quic.get_next_available_stream_id(
                is_unidirectional=True
            )

        elif isinstance(event, StreamDataReceived):
            self._buffers[event.stream_id] += event.data

            if event.end_stream:
                self._process_stream(event.stream_id)

        elif isinstance(event, ConnectionTerminated):
            print("[QUIC] Connection terminated")
            self._done.set()

    def _process_stream(self, stream_id: int):
        raw = bytes(self._buffers.pop(stream_id, b""))
        if len(raw) < HEADER_SIZE:
            return  # too short for header

        # Parse frame header: [4B frame_id][4B payload_size][8B send_timestamp]
        frame_id, payload_size, tx_time = struct.unpack(HEADER_FMT, raw[:HEADER_SIZE])
        payload = raw[HEADER_SIZE:]

        if len(payload) != payload_size:
            print(f"[WARN] stream={stream_id} frame={frame_id}: "
                  f"expected {payload_size}B got {len(payload)}B")
            self._send_control(frame_id, "Nack")
            return

        frame_recv_time = time.perf_counter()
        self._current_fps = 1.0 / max(frame_recv_time - self._previous_time, 1e-6)
        self._previous_time = frame_recv_time

        # Decode H.264
        frame = decode_h264(payload)
        if frame is None:
            print(f"[WARN] frame={frame_id}: H.264 decode failed")
            self._send_control(frame_id, "Nack")
            return

        # Read QR code
        detected_id, qr_data = read_qr(frame)

        # Save frame — the viewer process picks the newest one up from here
        cv2.imwrite(os.path.join(RECV_DIR, f"{frame_id:04d}.png"), frame)

        with open(FRAME_LOG, "a") as f:
            f.write(f"{frame_id},{payload_size},{frame_recv_time:.6f},{self._current_fps:.2f}\n")

        self._frame_count += 1
        n_cmds = 0

        # Ack every ACK_FREQ frames
        if self._frame_count % ACK_FREQ == 0:
            self._send_control(frame_id, "Ack")

        # Send joystick command if sync file has one for this frame position
        matching = sync_df[sync_df["ID"] == self._frame_counter]
        if not matching.empty:
            encrypted_cmds = matching["encrypted_cmd"].values
            n_cmds = len(encrypted_cmds)
            self._send_control(frame_id, "command",
                               cmd=str(encrypted_cmds[0]),
                               number=n_cmds)
            cmd_send_time = time.perf_counter()
            self._cmd_count += n_cmds
            response_time_ms = (cmd_send_time - frame_recv_time) * 1000.0
            print(f"[CMD] frame={frame_id:04d}  commands={n_cmds}  rt={response_time_ms:.1f}ms")
            with open(RT_LOG, "a") as f:
                f.write(f"{frame_id},{frame_recv_time:.6f},{cmd_send_time:.6f}\n")
        else:
            response_time_ms = 0.0

        print(f"[RX]  frame={frame_id:04d}  qr={detected_id:4}  "
              f"size={payload_size:7d}B  fps={self._current_fps:5.1f}")

        with open(EVENT_LOG, "a") as f:
            f.write(f"{frame_recv_time:.6f},FRAME,{frame_id},{payload_size},{self._current_fps:.2f},{n_cmds},{response_time_ms:.1f}\n")

        self._frame_counter += 1

        if frame_id >= STOP_FRAME - 1:
            duration = time.perf_counter() - self._session_start
            avg_fps = self._frame_count / max(duration, 1e-6)
            print(f"\n[SUMMARY] frames={self._frame_count}  commands={self._cmd_count}"
                  f"  avg_fps={avg_fps:.1f}  duration={duration:.1f}s")
            cv2.destroyAllWindows()
            self._done.set()

    def _send_control(self, frame_id: int, msg_type: str,
                      cmd: str = "0", number: int = 0):
        if self._ctrl_stream_id is None:
            return
        now = time.perf_counter()
        self._current_cps = 1.0 / max(now - self._cmd_previous_time, 1e-6)
        self._cmd_previous_time = now

        message = (f"{now},{cmd},{frame_id},{msg_type},"
                   f"{number},{self._current_fps:.4f},{self._current_cps:.4f}")
        self._quic.send_stream_data(self._ctrl_stream_id, message.encode())
        self.transmit()

        with open(RATE_LOG, "a") as f:
            f.write(f"{frame_id},{self._current_fps:.4f},{self._current_cps:.4f}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    configuration = QuicConfiguration(is_client=True)
    configuration.verify_mode = False          # self-signed cert in dev
    configuration.alpn_protocols = ["cgquic"]

    print(f"[QUIC] Connecting to {SERVER_IP}:{QUIC_PORT}")
    print(f"[QUIC] Game={GAME}  stop_frame={STOP_FRAME}  sync={SYNC_FILE}  commands={len(sync_df)}")

    async with connect(
        host=SERVER_IP,
        port=QUIC_PORT,
        configuration=configuration,
        create_protocol=QUICReceiverProtocol,
    ) as protocol:
        await protocol._done.wait()

    print("[QUIC] Receiver finished.")


def run_player():
    """Synchronous entry point.

    Launches the live video window as a separate system-Python process (GTK
    OpenCV, which renders as root inside Mininet), then runs the QUIC asyncio
    logic in this thread. The viewer is told to stop when the stream finishes.
    """
    viewer = None
    if LIVE_WATCHING:
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
        try:
            viewer = subprocess.Popen([SYS_PY, VIEWER_PATH, os.path.abspath(RECV_DIR)])
            print("[QUIC] Live viewer started (system Python / GTK)")
        except Exception as e:
            print(f"[WARN] could not start live viewer: {e}")

    try:
        asyncio.run(main())
    finally:
        if viewer is not None:
            open(STOP_FILE, "w").close()   # signal the viewer to exit
            try:
                viewer.wait(timeout=3)
            except subprocess.TimeoutExpired:
                viewer.terminate()


if __name__ == "__main__":
    try:
        run_player()
    except KeyboardInterrupt:
        print("\n[QUIC] Receiver stopped.")
