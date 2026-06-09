# CGReplay UI

Web-based control interface for the [CGReplay](https://github.com/dcomp-leris/CGReplay)
cloud gaming network emulator (UFSCar LERIS Lab).

**Works on Linux and Windows.** No Mininet required to run the UI in demo mode.
Set `USE_REAL_MININET=1` on a Linux machine with Mininet installed to run real experiments.

---

## Project structure

```
cgreplay-ui/
├── backend/
│   ├── server.py          ← FastAPI + WebSocket server
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx        ← Full React dashboard
│   │   ├── main.jsx
│   │   └── index.css
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── config/
│   └── config.yaml        ← CGReplay configuration
├── scripts/
│   ├── setup_linux.sh     ← One-time setup (Linux/macOS)
│   ├── setup_windows.ps1  ← One-time setup (Windows)
│   └── run.sh             ← Start both servers (Linux)
└── README.md
```

---

## Quick start

### Prerequisites

| Tool | Minimum version | Install |
|------|----------------|---------|
| Python | 3.9+ | https://python.org |
| Node.js | 18+ | https://nodejs.org |
| npm | 9+ | bundled with Node |
| git | any | https://git-scm.com |

---

### Linux / macOS

```bash
# 1. Clone this repo
git clone <this-repo-url>
cd cgreplay-ui

# 2. One-time setup (installs all deps)
bash scripts/setup_linux.sh

# 3a. Start everything at once
bash scripts/run.sh

# — OR start manually in two terminals —

# Terminal 1 — backend
cd backend
source .venv/bin/activate
python server.py

# Terminal 2 — frontend
cd frontend
npm run dev

# 4. Open browser
#    http://localhost:3000
```

---

### Windows (PowerShell)

```powershell
# 1. Clone
git clone <this-repo-url>
cd cgreplay-ui

# 2. Setup (run once)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup_windows.ps1

# 3a. Backend (Terminal 1)
cd backend
.\.venv\Scripts\Activate.ps1
python server.py

# 3b. Frontend (Terminal 2)
cd frontend
npm run dev

# 4. Open http://localhost:3000
```

---

## Running with real Mininet (Linux only)

### Install Mininet

```bash
# Option A — from package (Ubuntu 20.04+)
sudo apt-get update
sudo apt-get install -y mininet

# Option B — from source (recommended)
git clone https://github.com/mininet/mininet
cd mininet
sudo PYTHON=python3 util/install.sh -nfv

# Verify
sudo mn --test pingall
```

### Install CGReplay

```bash
git clone https://github.com/dcomp-leris/CGReplay.git
cd CGReplay
pip3 install -r requirements.txt  # if it exists
```

### Configure

Edit `config/config.yaml` to set your server/player IPs and paths:

```yaml
server:
  server_IP: "10.0.0.1"
game:
  capture_dir: "/path/to/CGReplay/captures"
  results_dir: "/path/to/CGReplay/results"
```

Also update the **Config file path** field in the UI to point to your
`CGReplay/config/config.yaml`.

### Enable real Mininet

```bash
cd backend
source .venv/bin/activate
USE_REAL_MININET=1 python server.py
```

The backend will now issue real `mn` and `python3 CGReplay/replay.py` commands
when you click "start simulation."

---

## Applying the bottleneck TC rules

After Mininet starts the topology, apply traffic control rules on the last link
manually (or let CGReplay handle it):

```bash
# In the Mininet CLI (or xterm on H0/S_last)
# Identify the bottleneck interface, e.g. s2-eth2

# Set bandwidth + loss on bottleneck link
sudo tc qdisc add dev s2-eth2 root handle 1: tbf rate 10mbit burst 10k latency 400ms
sudo tc qdisc add dev s2-eth2 parent 1:1 handle 10: netem loss 1% delay 20ms jitter 5ms
```

The UI sends the configured values; you can paste them into the Mininet terminal.

---

## UI features

### Network conditions panel
- **Delay / Jitter / Bandwidth** — applied to ALL links in the path
- **Pkt loss** — applied to last link only (bottleneck → player)
- **Bottleneck bandwidth** — last link BW to create congestion

### Codec & transport
- Codecs: H.264, H.265, VP9
- Transport protocols: UDP/RTP (WebRTC), QUIC, SCReAM
- Frame rate: 30 / 60 / 90 / 120 fps
- Resolution: 640×480 / 1280×720 / 1920×1080

### QoE metrics (live charts)
| Metric | What it measures |
|--------|-----------------|
| Response Time | Round-trip latency for commands (ms) |
| VMAF | Video Multi-Method Assessment Fusion (0–100) |
| SSIM | Structural Similarity Index (0–1) |
| PSNR | Peak Signal-to-Noise Ratio (dB) |

In demo mode these are computed from your network parameters.
With real CGReplay they come from `ffmpeg -vf vmaf` / SSIM measurements.

### Cloud gaming session
Live canvas renders a simulated game stream with:
- Codec/protocol/FPS overlay
- Packet-loss glitch artifacts when loss > 3%
- Command log with uplink/downlink directions and latency

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Backend HTTP/WS port |
| `USE_REAL_MININET` | `0` | Set to `1` to run real `mn` + CGReplay |

---

## Integrating real metric collection

Replace the `compute_metrics()` function in `backend/server.py` with
real measurement code. Example using ffmpeg VMAF:

```python
import subprocess, re

def measure_vmaf(ref_video: str, dist_video: str) -> float:
    result = subprocess.run([
        "ffmpeg", "-i", dist_video, "-i", ref_video,
        "-lavfi", "libvmaf=log_fmt=json:log_path=/tmp/vmaf.json",
        "-f", "null", "-"
    ], capture_output=True, text=True)
    import json
    data = json.load(open("/tmp/vmaf.json"))
    return data["pooled_metrics"]["vmaf"]["mean"]
```

For PSNR / SSIM:
```bash
ffmpeg -i distorted.mp4 -i reference.mp4 \
  -lavfi "psnr=stats_file=/tmp/psnr.log;[0:v][1:v]ssim=stats_file=/tmp/ssim.log" \
  -f null -
```

---

## Troubleshooting

**Backend not reachable (demo mode banner shows)**
- Check backend is running: `curl http://localhost:8000/api/status`
- Check port 8000 is not blocked by firewall

**`mn` command not found**
- Mininet must be installed on Linux
- On Windows, run in demo mode only (no Mininet on Windows)

**Permission denied running mn**
- Mininet requires root: `sudo python server.py` or configure sudoers

**Charts not updating**
- WebSocket must be connected (green dot top-right)
- In demo mode the simulation runs fully in the browser — no backend needed

**Frontend won't start (npm error)**
- `node --version` must be ≥ 18
- Try: `npm cache clean --force && npm install`

---

## Citation

If you use CGReplay in your research:

```
Shirmarz, A., de Castro, A. G., Verdi, F. L., & Rothenberg, C. E. (2025).
CGReplay: Capture and Replay of Cloud Gaming Traffic for QoE/QoS Assessment.
arXiv preprint arXiv:2505.11973.
```
