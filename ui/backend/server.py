"""
CGReplay UI — Backend Server
FastAPI + WebSocket server that interfaces with Mininet and CGReplay.
Run with: python backend/server.py
"""

import asyncio
import csv
import glob
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="CGReplay++ Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files (frontend build) ────────────────────────────────────────────
FRONTEND_BUILD = Path(__file__).parent.parent / "frontend" / "build"
if FRONTEND_BUILD.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_BUILD), html=True), name="static")

# ── In-memory simulation state ────────────────────────────────────────────────
class SimState:
    def __init__(self):
        self.running = False
        self.proc: Optional[subprocess.Popen] = None
        self.ws_clients: list[WebSocket] = []

sim = SimState()


# ── CGReplay integration ──────────────────────────────────────────────────────
# Maps the UI configuration to the real CGReplay Mininet pipeline and streams the
# measured metrics back. Demo mode (USE_REAL_MININET unset) is left untouched.
CGREPLAY_DIR = Path(os.environ.get(
    "CGREPLAY_DIR", Path(__file__).resolve().parent.parent.parent / "CGReplay"))
TOPOLOGY     = CGREPLAY_DIR / "topology" / "simple_topology.py"
METRICS_DIR  = CGREPLAY_DIR / "player" / "logs"
KOMBAT_DIR   = CGREPLAY_DIR / "server" / "Kombat"
RECV_DIR     = CGREPLAY_DIR / "player" / "received_frames"
CONFIG_YAML  = CGREPLAY_DIR / "config" / "config.yaml"
SYS_PYTHON   = "/usr/bin/python3"   # interpreter that has Mininet

_PROTO_MAP = {
    "UDP/RTP (WebRTC)": "rtp", "UDP/RTP": "rtp", "UDP": "rtp", "RTP": "rtp",
    "QUIC": "quic", "SCReAM": "scream", "SCREAM": "scream",
}
_CODEC_MAP = {"H.264": "h264", "H264": "h264", "H.265": "h265", "H265": "h265"}


def _map_protocol(proto: str) -> Optional[str]:
    return _PROTO_MAP.get(proto, _PROTO_MAP.get(proto.split("(")[0].strip()))


def _dataset_frame_count() -> int:
    n = len(glob.glob(str(KOMBAT_DIR / "*.png")))
    return n if n > 0 else 120


def _to_float(x):
    try:
        if x is None or x == "" or str(x).lower() == "nan":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _read_live_watching() -> str:
    """Return the current Running.live_watching value ('True'/'False')."""
    try:
        m = re.search(r"^\s*live_watching:\s*(\w+)", CONFIG_YAML.read_text(), re.M)
        return m.group(1) if m else "True"
    except Exception:
        return "True"


def _set_live_watching(value: str):
    """Set Running.live_watching in config.yaml (no native window for web runs)."""
    if CONFIG_YAML.exists():
        os.system(f"sed -i 's/^    live_watching: .*/    live_watching: {value}/' {CONFIG_YAML}")


async def run_real_experiment(cfg: dict):
    """Run the real CGReplay Mininet experiment and stream measured metrics.

    Maps the UI config to topology/simple_topology.py, streams its stdout as log
    lines, then reads player/logs/metrics_<proto>.csv and emits one tick per
    frame with the real VMAF / SSIM / PSNR / response time.
    """
    proto   = _map_protocol(cfg.get("proto", ""))
    codec   = cfg.get("codec", "H.264")
    encoder = _CODEC_MAP.get(codec)

    if proto not in ("rtp", "quic", "scream"):
        await broadcast({"type": "error", "msg": f"Protocol '{cfg.get('proto')}' is not supported by CGReplay (use UDP/RTP, QUIC or SCReAM)."})
        await broadcast({"type": "done", "msg": "aborted — unsupported protocol"})
        sim.running = False
        return
    if encoder is None:
        await broadcast({"type": "error", "msg": f"Codec '{codec}' is not supported by CGReplay (use H.264 or H.265; VP9 is not wired)."})
        await broadcast({"type": "done", "msg": "aborted — unsupported codec"})
        sim.running = False
        return

    fps      = int(float(cfg.get("fps", 30)))
    duration = int(float(cfg.get("duration", 4)))
    bw       = float(cfg.get("bn_bw", cfg.get("bw", 10)))   # bottleneck is the constraint
    delay    = float(cfg.get("delay", 10))
    loss     = float(cfg.get("pkt_loss", 0))

    # Cap the run to the available dataset (Kombat has a fixed number of frames).
    available = _dataset_frame_count()
    want      = duration * fps + 1
    frames    = min(want, available)
    if want > available:
        await broadcast({"type": "log", "level": "warn",
                         "msg": f"> Dataset has {available} frames (~{available // fps}s); capping {duration}s run to {frames - 1} frames."})

    cmd = [SYS_PYTHON, str(TOPOLOGY), "--protocol", proto, "--encoder", encoder,
           "--bw", str(bw), "--delay", f"{delay}ms", "--loss", str(loss),
           "--frames", str(frames)]
    if os.geteuid() != 0:
        await broadcast({"type": "log", "level": "warn",
                         "msg": "> Backend is not root; trying 'sudo -n'. If it fails, restart the backend with sudo."})
        cmd = ["sudo", "-n"] + cmd

    await broadcast({"type": "log", "level": "info", "msg": f"$ {' '.join(cmd)}"})
    await broadcast({"type": "log", "level": "info",
                     "msg": f"> Codec {codec} ({encoder}) | Protocol {cfg.get('proto')} ({proto}) | bottleneck {bw:g}Mbps {delay:g}ms {loss:g}% loss"})
    if float(cfg.get("jitter", 0)):
        await broadcast({"type": "log", "level": "warn",
                         "msg": "> Note: jitter is not applied by the current topology (ignored)."})
    await broadcast({"type": "log", "level": "warn",
                     "msg": "> Launching real Mininet experiment — full stream + metrics, ~1 min..."})

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("DISPLAY", ":0")

    # Turn off CGReplay's native live-view window — frames are streamed to the
    # web canvas instead (via /api/frame). Restored after the run.
    _orig_live = _read_live_watching()
    _set_live_watching("False")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(CGREPLAY_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
    except FileNotFoundError as e:
        _set_live_watching(_orig_live)
        await broadcast({"type": "error", "msg": f"Could not launch topology: {e}"})
        await broadcast({"type": "done", "msg": "aborted — launch failed"})
        sim.running = False
        return
    sim.proc = proc

    async for raw in proc.stdout:
        if not sim.running:
            proc.terminate()
            break
        line = raw.decode(errors="replace").rstrip()
        if not line or line.startswith("Processing ") or "Missing:" in line:
            continue
        await broadcast({"type": "log", "level": "info", "msg": line})
    await proc.wait()
    sim.proc = None
    _set_live_watching(_orig_live)   # restore the user's config

    if not sim.running:
        await broadcast({"type": "stopped", "msg": "Experiment stopped by user."})
        return

    metrics_csv = METRICS_DIR / f"metrics_{proto}.csv"
    if not metrics_csv.exists():
        await broadcast({"type": "error", "msg": f"No metrics file produced: {metrics_csv}"})
        await broadcast({"type": "done", "msg": "no metrics produced"})
        sim.running = False
        return

    with open(metrics_csv) as f:
        rows = list(csv.DictReader(f))

    received = sum(1 for r in rows if _to_float(r.get("SSIM")) is not None)
    await broadcast({"type": "log", "level": "ok",
                     "msg": f"> Streaming real metrics: {received}/{len(rows)} frames from metrics_{proto}.csv"})

    total = len(rows)
    last_rt = None   # response time is event-based (only at command frames);
                     # forward-fill so the RT line is continuous, not empty
    for i, r in enumerate(rows, 1):
        if not sim.running:
            break
        vmaf   = _to_float(r.get("VMAF"))
        ssim   = _to_float(r.get("SSIM"))
        psnr   = _to_float(r.get("PSNR"))
        rt_raw = _to_float(r.get("response_time_ms"))   # measured this frame?
        if rt_raw is not None:
            last_rt = rt_raw
        rt = last_rt
        t  = int(_to_float(r.get("frame_id")) or i)
        metrics = {
            "rt":   round(rt)      if rt   is not None else None,
            "vmaf": round(vmaf, 1) if vmaf is not None else None,
            "ssim": round(ssim, 3) if ssim is not None else None,
            "psnr": round(psnr, 1) if psnr is not None else None,
        }
        msg = {"type": "tick", "t": t, "duration": total, "metrics": metrics}
        if rt_raw is not None:   # log a command only when actually measured
            msg["command"] = {"time": t, "cmd": "command", "dir": "UP",
                              "latency": round(rt_raw), "dropped": False}
        await broadcast(msg)
        await asyncio.sleep(0.03)

    await broadcast({"type": "done",
                     "msg": f"Real run complete — {proto}/{encoder}, {received}/{total} frames received"})
    sim.running = False
    sim.proc = None


# ── WebSocket broadcast ───────────────────────────────────────────────────────
async def broadcast(msg: dict):
    dead = []
    for ws in sim.ws_clients:
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.append(ws)
    for ws in dead:
        sim.ws_clients.remove(ws)


# ── Metric computation (derived from network params) ─────────────────────────
def compute_metrics(cfg: dict, t: int) -> dict:
    """
    Simulate realistic QoE metrics based on network conditions.
    Replace this function with actual measurement collection when
    running CGReplay for real.
    """
    bw       = float(cfg.get("bw", 100))
    bn_bw    = float(cfg.get("bn_bw", 10))
    delay    = float(cfg.get("delay", 20))
    jitter   = float(cfg.get("jitter", 5))
    loss     = float(cfg.get("pkt_loss", 1))
    codec    = cfg.get("codec", "H.264")
    proto    = cfg.get("proto", "UDP/RTP (WebRTC)")

    congestion   = max(0.0, min(1.0, 1.0 - bn_bw / 100.0))
    codec_boost  = {"H.265": 3, "VP9": 2, "H.264": 0}.get(codec, 0)
    proto_boost  = {"SCReAM": 4, "QUIC": 2, "UDP/RTP (WebRTC)": 0}.get(proto, 0)
    rng          = random.Random(t * 7 + int(loss * 13))

    rt   = round(delay * 2 + jitter * 2 + loss * 5 + congestion * 80 + rng.uniform(-10, 10))
    vmaf = round(min(100, max(5,  95 - loss * 4 - congestion * 40 + codec_boost + proto_boost + rng.uniform(-2, 2))))
    ssim = round(min(1.0, max(0.4, 0.97 - loss * 0.02 - congestion * 0.15 + rng.uniform(-0.01, 0.01))), 3)
    psnr = round(min(50,  max(18,  42  - loss * 1.5 - congestion * 10  + codec_boost * 0.5 + rng.uniform(-1, 1))), 1)

    return {"rt": rt, "vmaf": vmaf, "ssim": ssim, "psnr": psnr}


COMMANDS = [
    "↑ forward", "↓ back", "← left", "→ right",
    "A attack",  "B jump",  "X reload", "Y special",
    "LT aim",    "RT fire", "LB shield", "RB dodge",
]

def fake_command(cfg: dict, t: float) -> dict:
    delay = float(cfg.get("delay", 20))
    loss  = float(cfg.get("pkt_loss", 1))
    latency = round(delay * 2 + random.uniform(0, 20))
    dropped = random.random() < (loss / 100)
    return {
        "time":    round(t, 1),
        "cmd":     random.choice(COMMANDS),
        "dir":     random.choice(["UP", "DN"]),
        "latency": latency,
        "dropped": dropped,
    }


# ── Mininet topology builder ──────────────────────────────────────────────────
def build_mn_command(cfg: dict) -> list[str]:
    """
    Build the Mininet CLI command from the configuration dict.
    This is what would be executed on the Linux machine.
    """
    n_sw   = int(cfg.get("n_switches", 2))
    bw     = float(cfg.get("bw", 100))
    bn_bw  = float(cfg.get("bn_bw", 10))
    delay  = float(cfg.get("delay", 20))
    jitter = float(cfg.get("jitter", 5))
    loss   = float(cfg.get("pkt_loss", 1))
    topo   = cfg.get("topo_type", "linear")

    # Example: mn --topo linear,2 --link tc,bw=100,delay=20ms
    # Bottleneck is configured separately via TC rules on the last link.
    cmd = [
        "sudo", "python3", "-m", "mininet.cli",
        "--topo", f"{topo},{n_sw}",
        "--link", f"tc,bw={bw},delay={delay}ms,jitter={jitter}ms",
    ]
    return cmd


def build_replay_command(cfg: dict) -> list[str]:
    """
    Build the CGReplay replay.py command.
    Adjust paths to match your CGReplay installation.
    """
    cgreplay_path = cfg.get("cgreplay_path", "CGReplay")
    codec    = cfg.get("codec", "H.264").lower().replace(".", "")
    proto    = cfg.get("proto", "UDP/RTP (WebRTC)").split("(")[0].strip().lower().replace("/", "_")
    fps      = cfg.get("fps", "30")
    res      = cfg.get("res", "640x480").replace("×", "x")
    game     = cfg.get("game", "Fortnite").lower()
    duration = int(cfg.get("duration", 30))

    cmd = [
        "sudo", "python3", f"{cgreplay_path}/replay.py",
        "--codec",    codec,
        "--protocol", proto,
        "--fps",      fps,
        "--res",      res,
        "--game",     game,
        "--duration", str(duration),
        "--config",   cfg.get("config_path", f"{cgreplay_path}/config/config.yaml"),
    ]
    return cmd


# ── Simulation runner (async) ─────────────────────────────────────────────────
async def run_simulation(cfg: dict):
    """
    Simulation loop. In a real deployment this would:
    1. Call Mininet to bring up the topology
    2. Apply TC rules for bottleneck link
    3. Start CGReplay server on H0, player on H1
    4. Stream real metrics from output files / sockets

    For now it runs a realistic emulation of the metric stream.
    Set USE_REAL_MININET=1 to attempt actual subprocess execution.
    """
    sim.running = True

    # Real CGReplay experiment path: run the actual Mininet topology + metrics.
    if os.environ.get("USE_REAL_MININET", "0") == "1":
        await run_real_experiment(cfg)
        return

    duration   = int(cfg.get("duration", 30))
    use_real   = False  # simulated path below
    n_switches = int(cfg.get("n_switches", 2))

    mn_cmd     = build_mn_command(cfg)
    replay_cmd = build_replay_command(cfg)

    # ── Log startup ──────────────────────────────────────────────────────────
    await broadcast({"type": "log", "level": "info",
                     "msg": f"$ {' '.join(mn_cmd)}"})
    await asyncio.sleep(0.3)
    await broadcast({"type": "log", "level": "ok",
                     "msg": f"> Mininet topology: linear,{n_switches} | H0 ↔ {'↔ '.join(f'S{i}' for i in range(1,n_switches+1))} ↔ H1"})
    await asyncio.sleep(0.3)
    await broadcast({"type": "log", "level": "info",
                     "msg": f"> Codec: {cfg.get('codec')} | Protocol: {cfg.get('proto')} | Game: {cfg.get('game')}"})
    await asyncio.sleep(0.3)

    if use_real:
        # ── Real Mininet execution ────────────────────────────────────────────
        await broadcast({"type": "log", "level": "warn",
                         "msg": "> USE_REAL_MININET=1 — launching real Mininet..."})
        try:
            sim.proc = subprocess.Popen(
                mn_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            await broadcast({"type": "log", "level": "ok",
                             "msg": f"> Mininet PID {sim.proc.pid} started"})
        except FileNotFoundError:
            await broadcast({"type": "log", "level": "warn",
                             "msg": "> mn not found — falling back to simulation mode"})
            use_real = False

    # ── Metric stream loop ────────────────────────────────────────────────────
    for t in range(1, duration + 1):
        if not sim.running:
            break

        metrics = compute_metrics(cfg, t)
        cmd     = fake_command(cfg, t)

        await broadcast({
            "type":    "tick",
            "t":       t,
            "duration": duration,
            "metrics": metrics,
            "command": cmd,
        })

        if t % 5 == 0:
            qd = random.randint(0, 12)
            level = "warn" if qd > 8 else "ok"
            await broadcast({"type": "log", "level": level,
                             "msg": f"> t={t}s — frames OK, queue depth: {qd}, RTT: {metrics['rt']}ms"})

        await asyncio.sleep(1.0)

    # ── Teardown ──────────────────────────────────────────────────────────────
    if sim.proc:
        sim.proc.terminate()
        sim.proc = None

    if sim.running:
        await broadcast({"type": "done", "msg": "Simulation complete. Results saved to /results/"})
        await broadcast({"type": "log", "level": "ok",
                         "msg": "> Simulation complete. mn cleaned up."})
    else:
        await broadcast({"type": "stopped", "msg": "Simulation stopped by user."})

    sim.running = False


# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/api/status")
async def status():
    return {"running": sim.running, "clients": len(sim.ws_clients)}


@app.get("/api/frame")
async def latest_frame():
    """Serve the most-recent received game frame for the live web canvas.

    Reads the newest PNG in CGReplay/player/received_frames/, which the player
    writes in real time during a run. 204 when there is nothing to show yet.
    """
    try:
        pngs = glob.glob(str(RECV_DIR / "*.png"))
        if not pngs:
            return Response(status_code=204)
        newest = max(pngs, key=os.path.getmtime)
        with open(newest, "rb") as fh:
            data = fh.read()
        if not data:
            return Response(status_code=204)
        return Response(content=data, media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    except Exception:
        return Response(status_code=204)


@app.post("/api/stop")
async def stop():
    sim.running = False
    if sim.proc:
        sim.proc.terminate()
        sim.proc = None
    return {"ok": True}


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    sim.ws_clients.append(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("action") == "start":
                if sim.running:
                    await websocket.send_text(json.dumps(
                        {"type": "error", "msg": "Simulation already running"}))
                else:
                    asyncio.create_task(run_simulation(msg.get("config", {})))

            elif msg.get("action") == "stop":
                sim.running = False
                if sim.proc:
                    sim.proc.terminate()
                    sim.proc = None

    except WebSocketDisconnect:
        if websocket in sim.ws_clients:
            sim.ws_clients.remove(websocket)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n{'='*55}")
    print("  CGReplay++ Backend")
    print(f"  http://localhost:{port}")
    print(f"  WebSocket: ws://localhost:{port}/ws")
    print(f"  CGReplay dir: {CGREPLAY_DIR}")
    print(f"  Set USE_REAL_MININET=1 (as root) to run the real pipeline")
    print(f"{'='*55}\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
