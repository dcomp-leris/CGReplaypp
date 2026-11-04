# CGReplay++: Replaying Cloud Gaming Sessions to Evaluate UDP, QUIC, and SCReAM

**Replaying cloud gaming sessions to evaluate transport protocols (UDP, QUIC, RoQ, SCReAM) under controlled network conditions.**

**Note**: This demo paper was submitted to "34th ACM International Conference on Multimedia" and is under review.

[![Conference](https://img.shields.io/badge/Demo-Presnetation-blue)](https://docs.google.com/presentation/d/1vZZdlMLaBve1d6VJK2y9-KNPEcyZjNuUikISQc4mf00/edit?usp=sharing)
[![Conference](https://img.shields.io/badge/Demo-Video-yellow)](https://youtu.be/NaCQpPOez-k)
[![Conference](https://img.shields.io/badge/Demo-Paper-red)](https://github.com/dcomp-leris/CGReplaypp/blob/main/ACMMM_DEMO_2026_CGReplaypp.pdf)
---
UFSCar LERIS Lab. CGReplay++ extends [CGReplay](https://github.com/dcomp-leris/CGReplay) with multiple transports, a quality-of-experience (QoE) measurement pipeline (VMAF, SSIM, PSNR, response time), and a web dashboard to configure, run, and compare experiments.

CGReplay++ is not plain video streaming. The server streams recorded game frames to the player, and the player sends joystick commands back to the server. This interaction (commands and frames interleaved) is what makes it a faithful cloud gaming workload.

<img width="686" height="521" alt="image" src="https://github.com/user-attachments/assets/930ac394-3f17-4b91-9b48-e338b4081d81" />


---

## Transports

| Transport | Stack | Congestion control | Status |
|-----------|-------|--------------------|--------|
| UDP / RTP (WebRTC-style) | GStreamer (x264/x265) | GCC on RTCP feedback (TWCC or RFC 8888) + app-layer sync rate control | working |
| QUIC | aioquic + PyAV | none (reliable streams) | working |
| SCReAM v2 | GStreamer + `gstscream` (Ericsson) | SCReAM v2 (L4S) | working |
| RoQ (RTP over QUIC) | aioquic datagrams + PyAV | none (unreliable datagrams) | working |

Codecs: H.264 and H.265 (HEVC), selectable per run.

---

## Repository layout

```
CGReplaypp/
├── server/              # frame server + QUIC sender + game datasets
│   ├── cg_server1.py
│   ├── quic_sender.py
│   └── Kombat/ Forza/ Fortnite/   # recorded frame sequences (large, see Datasets)
├── player/              # player + QUIC receiver + received frames + logs
│   ├── cg_gamer1.py
│   └── quic_receiver.py
├── topology/
│   └── simple_topology.py   # Mininet topology + experiment driver
├── tools/
│   ├── post_process.py      # computes VMAF/SSIM/PSNR/response time/QoE
│   ├── plot_qoe.py          # comparison figures
│   ├── qoe.py               # QoE formula (single source of truth)
│   └── video_Quality.py     # per-frame metric kernels
├── config/
│   └── config.yaml          # encoder, resolution, fps, transport flags
├── ui/                  # CGReplay++ web dashboard (see ui/README.md)
│   ├── backend/         # FastAPI + WebSocket
│   └── frontend/        # React + Vite
└── README.md
```

---

## Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| Linux | Ubuntu 20.04+ | Mininet runs on Linux only |
| Python | 3.9+ | two interpreters used (see below) |
| Mininet | 2.3+ | network emulation, needs root |
| GStreamer | 1.16+ | UDP/RTP and SCReAM paths |
| Node.js | 18+ | web dashboard frontend |
| Rust + CMake | recent | building the SCReAM `gstscream` plugin |

Two Python interpreters are used by design:

- a **virtualenv** for the QUIC path and the tooling (`aioquic`, `PyAV`, `opencv`, `pandas`, `matplotlib`, `streamlit`)
- the **system Python** for the GStreamer (UDP/RTP, SCReAM) paths and for Mininet, which expect the system GStreamer bindings

---

## Install

### 1. Clone

```bash
git clone https://github.com/dcomp-leris/CGReplaypp.git
cd CGReplaypp
```

### 2. Mininet

```bash
sudo apt-get update
sudo apt-get install -y mininet
sudo mn --test pingall   # verify
```

### 3. GStreamer

```bash
sudo apt-get install -y \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  python3-gi gir1.2-gstreamer-1.0
```

### 4. Python virtualenv (QUIC + tooling)

```bash
python3 -m venv ~/venv
~/venv/bin/pip install \
  aioquic av opencv-python numpy pandas \
  matplotlib seaborn scikit-image PyYAML qrcode pyzbar streamlit
```

### 5. VMAF (static ffmpeg with libvmaf)

VMAF needs an `ffmpeg` built with `libvmaf`. Download a static build (or compile one) and point `tools/post_process.py` at it:

```bash
ffmpeg -filters | grep libvmaf   # must list libvmaf
```

### 6. SCReAM v2 plugin (for the SCReAM transport)

```bash
git clone https://github.com/EricssonResearch/scream.git
cd scream/gstscream
./scripts/build.sh    # builds the wrapper lib (-DV2) + the Rust plugin
```

This builds **SCReAM v2** (`ScreamV2Tx`), the algorithm in draft `rfc8298bis-screamv2`.

Then drop in the CGReplay++ pipeline scripts (appsrc input, encoder-aware,
tuned SCReAM rate band — see `scream-scripts/README.md`):

```bash
cp scream-scripts/sender.sh scream-scripts/receiver.sh \
   scream/gstscream/scripts/
```

### 7. RoQ

No extra install. RoQ reuses the QUIC virtualenv (`aioquic`, `PyAV`): RTP
packets ride in QUIC DATAGRAM frames (RFC 9683 datagram mode, unreliable),
while the joystick command channel stays on a reliable QUIC stream. See
`server/roq_sender.py` and `player/roq_receiver.py`.

### 8. Web dashboard

```bash
cd ui
bash scripts/setup_linux.sh   # creates backend/.venv and frontend/node_modules
```

---

## Datasets

The recorded frame sequences (`server/Kombat`, `server/Forza`, `server/Fortnite`)
are large (Kombat is ~176 MB) and are not stored in git. Download them separately
and place them under `server/`. Kombat is the default dataset (120 frames, 1280x720).

---

## Run

### Command line (one transport at a time)

```bash
# UDP / RTP
sudo python3 topology/simple_topology.py --protocol rtp    --encoder h264 --bw 10 --delay 10ms --loss 0

# QUIC
sudo python3 topology/simple_topology.py --protocol quic   --encoder h264 --bw 10 --delay 10ms --loss 0

# RoQ (RTP over QUIC datagrams)
sudo python3 topology/simple_topology.py --protocol roq    --encoder h264 --bw 10 --delay 10ms --loss 0

# SCReAM v2
sudo python3 topology/simple_topology.py --protocol scream --encoder h264 --bw 10 --delay 10ms --loss 0
```

Flags: `--protocol {rtp,quic,roq,scream}`, `--encoder {h264,h265}`, `--bw <Mbit>`, `--delay <ms>`, `--loss <%>`, `--frames <N>`, `--cc-feedback {twcc,rfc8888,none}` (rtp only).

```bash
# UDP / RTP with the two RTCP congestion-feedback variants (both drive GCC on the server)
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback twcc    --bw 10 --delay 10ms
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback rfc8888 --bw 10 --delay 10ms
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback none    --bw 10 --delay 10ms  # original
```

Each run writes per-frame metrics to `player/logs/metrics_<protocol>.csv`.

### Metrics and figures

```bash
python3 tools/post_process.py --mode quic   # VMAF/SSIM/PSNR/response time/QoE
python3 tools/plot_qoe.py                    # comparison figures across modes
```

### Web dashboard

Demo mode (simulated metrics, no Mininet, any OS):

```bash
cd ui
# backend
./backend/.venv/bin/python backend/server.py
# frontend (second terminal)
cd frontend && npm run dev
# open http://localhost:3000
```

Real mode (measured metrics, Linux + Mininet, root):

```bash
cd ui/backend
sudo USE_REAL_MININET=1 DISPLAY=:0 ./.venv/bin/python server.py
# frontend unchanged; open http://localhost:3000, pick a transport, click Start
```

Open **http://localhost:3000** (the frontend). The backend on :8000 is an API/WebSocket
server and has no home page (a 404 on `/` is expected).

See `ui/README.md` for the dashboard details.

---

## Guideline: run WebRTC and RoQ (and SCReAM) and collect the dataset

A step-by-step recipe for running the transports and getting logs + pcaps + the merged
RTP-frame / video-frame dataset. Details and column lists are in
[RTP-frame <-> video-frame dataset](#rtp-frame---video-frame-dataset).

| Transport | `--protocol` | Extra flag | Status |
|---|---|---|---|
| WebRTC-style UDP/RTP + GCC on RTCP TWCC | `rtp` | `--cc-feedback twcc` | tested |
| WebRTC-style UDP/RTP + GCC on RTCP RFC 8888 | `rtp` | `--cc-feedback rfc8888` | tested |
| WebRTC-style UDP/RTP, app-layer rate control only | `rtp` | `--cc-feedback none` | tested |
| RoQ (RTP over QUIC datagrams) | `roq` | – | tested |
| SCReAM v2 | `scream` | – | **not validated yet** (needs the Ericsson `gstscream` build; logging and dataset support are in place) |

### Step 0 — once per machine

```bash
# system Python (GStreamer path: WebRTC, SCReAM) — see Install, step 3
sudo apt-get install -y python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-libav tcpdump
gst-inspect-1.0 rtphdrexttwcc        # must exist for --cc-feedback twcc (GStreamer >= 1.20)

# venv (RoQ + tools) — see Install, step 4
~/venv/bin/pip install aioquic av opencv-python numpy pandas pyzbar scikit-image PyYAML qrcode

# optional
sudo apt-get install -y tshark       # decrypt RoQ pcaps -> RTP visible at the network vantage points
export CGREPLAY_FFMPEG=/path/to/ffmpeg-with-libvmaf   # VMAF (otherwise skipped)
~/venv/bin/pip install lpips torch                    # LPIPS (otherwise skipped)
```

RoQ needs a TLS certificate at `protocols.quic_cert` / `quic_key` in `config/config.yaml`
(default `../../rtp_stream_creation/server.pem|key`, relative to `server/`):

```bash
mkdir -p ../rtp_stream_creation && openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout ../rtp_stream_creation/server.key -out ../rtp_stream_creation/server.pem -subj "/CN=cgreplay"
```

### Step 1 — put the game frames in place

`server/Kombat/`, `server/Forza/`, `server/Fortnite/` (PNG frames `0001.png ...`, see
[Datasets](#datasets)); the sync files are already in `server/syncs/` and `player/syncs/`.
Pick the game per run with `--game`.

### Step 2 — run one experiment

```bash
cd CGReplaypp
# WebRTC-style, GCC on TWCC feedback
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback twcc    --game Kombat \
     --encoder h264 --frames 300 --bw 10 --bn-bw 4 --delay 10ms --loss 0 --skip-metrics
# WebRTC-style, GCC on RFC 8888 feedback
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback rfc8888 --game Kombat \
     --encoder h264 --frames 300 --bw 10 --bn-bw 4 --delay 10ms --loss 0 --skip-metrics
# WebRTC-style, application-layer rate control only (original behaviour)
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback none    --game Kombat \
     --encoder h264 --frames 300 --bw 10 --bn-bw 4 --delay 10ms --loss 0 --skip-metrics
# RoQ
sudo python3 topology/simple_topology.py --protocol roq --game Kombat \
     --encoder h264 --frames 300 --bw 10 --bn-bw 4 --delay 10ms --loss 0 --skip-metrics
# SCReAM (not validated yet)
# sudo python3 topology/simple_topology.py --protocol scream --game Kombat --frames 300 --bw 10 --bn-bw 4 --delay 10ms --skip-metrics
```

* `--bn-bw` = bottleneck on the server uplink (what congestion control reacts to), `--bw` = access link,
  `--jitter 5ms` adds jitter, `--loss` is in %.
* `--skip-metrics` skips the old `post_process.py` step; the dataset builder (step 4) computes quality itself.
* `--no-frames` does not keep the received PNGs (saves ~1-2 MB/frame, but then no quality metrics).
* After an aborted run: `sudo mn -c` and check that `SCReAM`, `QUIC` and `RoQ` are `False` in `config/config.yaml`.

### Step 3 — what one run produces

The run is collected in `results/<game>_<transport>_<codec>_<bn>M_<date>/`
(e.g. `results/Kombat_webrtc-twcc_h264_4M_20261103-201500/`):

| Folder / file | Content |
|---|---|
| `run.json`, `config.yaml` | experiment parameters, exact config, git commit |
| `server_logs/srv_rtp.csv` | every RTP packet the server sent (seq, RTP ts, marker, sizes, NAL/FU info, frame id) |
| `server_logs/srv_video_frames.csv` | every frame the server pushed: frame id, QR timestamp, push time, RTP ts, app/applied bitrate |
| `server_logs/srv_QoEMetrics.csv`, `srv_codec_bitrate.csv`, `srv_frame.csv` | application-layer sync / rate-control logs (frames <-> commands) |
| `server_logs/srv_gcc.csv`, `srv_rate_combined.csv` | GCC state per feedback, app vs GCC vs applied bitrate (WebRTC twcc / rfc8888) |
| `player_logs/ply_video_frames.csv` | every decoded frame: decode order, receive time, saved PNG |
| `player_logs/ratelog_*.csv`, `responsetime_CG.csv`, `ply_*frame.csv` | player fps/cps, frame -> command response time |
| `player_logs/ply_rtp.csv`, `quic_keylog.txt` | RoQ only: every RTP datagram received, QUIC TLS secrets |
| `pcap/s1-eth1.pcap`, `pcap/s1-eth2.pcap` | network device: switch port facing the server / the player |
| `pcap/h2.pcap` | player NIC: video, commands, RTCP feedback (WebRTC) or QUIC (RoQ) |
| `received_frames/` | decoded frames (PNG) for the quality metrics |

### Step 4 — build the dataset

```bash
~/venv/bin/python3 tools/build_rtp_dataset.py results/Kombat_webrtc-twcc_h264_4M_* \
     results/Kombat_roq_h264_4M_* --combined results/kombat.csv
~/venv/bin/python3 tools/verify_rtp_mapping.py results/Kombat_webrtc-twcc_h264_4M_*   # optional check (WebRTC/SCReAM)
```

Per run it writes `dataset/rtp_packets.csv`, `rtp_frames.csv`, `video_frames.csv`,
`player_frames.csv`, `commands.csv`, `other_packets.csv`; `--combined` concatenates the
`video_frames.csv` of all runs (one row per server video frame, with run, game and
transport columns) for the analysis of network features vs receiver-side quality.
For RoQ, install `tshark` so the builder can decrypt the pcaps with `quic_keylog.txt`;
without it RoQ RTP comes only from the endpoint logs (`server_tx`, `player_rx`).

### Step 5 — every game x transport in one go

```bash
sudo GAMES="Kombat Forza Fortnite" TRANSPORTS="webrtc-twcc webrtc-rfc8888 webrtc-none roq" \
     FRAMES=300 BW=10 BN_BW=4 DELAY=10ms LOSS=0 REPEAT=1 tools/run_matrix.sh
```

It runs every combination, collects each run as in step 3 and finishes with
`results/dataset_<date>.csv` (all runs merged). Add `scream` to `TRANSPORTS` once SCReAM is
validated. Other knobs: `ENCODER=h265`, `JITTER=5ms`, `NO_FRAMES=1`, `RESULTS=<dir>`,
`PY_TOOLS=<python with pandas/opencv/pyzbar>`.

---

## Configuration

`config/config.yaml` controls encoder, resolution, frame rate, bitrate, and the
transport flags. The topology driver and the dashboard write the relevant fields
for each run, so editing it by hand is only needed for advanced cases.

### UDP/RTP congestion control: RTCP feedback + GCC

The UDP/RTP path has two rate controllers that work together:

1. **Application layer (unchanged):** the server compares the frame it is streaming
   with the frame ID in the player's Ack/Nack/command messages (`sync` section) and
   raises or lowers its target bitrate (jump/rise/decrease/fall).
2. **Network layer (new):** the player sends RTCP congestion feedback, and the server
   runs GCC (draft-ietf-rmcat-gcc: trendline delay-gradient filter, adaptive-threshold
   overuse detector, AIMD, loss-based controller) on it (`server/gcc_controller.py`).

The encoder runs at `min(app-layer rate, GCC rate)` and is updated live
(`x264enc`/`x265enc` `bitrate`). Pick the feedback format with
`congestion_control.feedback` in `config/config.yaml`, `--cc-feedback`, or the
`CGREPLAY_CC_FEEDBACK` environment variable:

| `feedback` | RTCP message | How it is produced |
|------------|--------------|--------------------|
| `twcc` | RTPFB FMT=15, transport-wide CC (draft-holmer-rmcat-transport-wide-cc-extensions-01) | GStreamer `rtpbin` on both ends; needs GStreamer >= 1.20 (falls back to `none` otherwise) |
| `rfc8888` | RTPFB FMT=11, CCFB (RFC 8888) | Python: `player/rfc8888_receiver.py` (a small relay timestamps each RTP packet and sends reports), `server/rfc8888.py` (parses them) |
| `none` | none | original behaviour, application-layer rate control only |

Extra server logs: `server/logs/srv_gcc.csv` (GCC state per feedback) and
`server/logs/srv_rate_combined.csv` (per frame: app rate, GCC rate, applied rate).
Feedback uses UDP ports 5011 (server) and 5012/5013 (player), set in `congestion_control`.

#### How to run it

**1. Pick the feedback mode** in `config/config.yaml` (or override it per run, see 2 and 3):

```yaml
congestion_control:
    feedback: "twcc"      # "twcc" | "rfc8888" | "none"
```

The UDP/RTP path is used when `protocols.SCReAM`, `protocols.QUIC` and `protocols.RoQ`
are all `False` (the topology driver sets them for `--protocol rtp`).

**2. With the Mininet topology** (recommended; shapes the link and records the pcap):

```bash
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback twcc    --encoder h264 --bw 10 --bn-bw 4 --delay 10ms --loss 0
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback rfc8888 --encoder h264 --bw 10 --bn-bw 4 --delay 10ms --loss 0
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback none    --encoder h264 --bw 10 --bn-bw 4 --delay 10ms --loss 0
```

`--bn-bw` is the bottleneck on the server uplink, the link GCC reacts to. Leave
`--cc-feedback` out to use the value from `config.yaml`.

**3. By hand, two hosts (or two shells)** with the IPs from `config.yaml`
(`server.server_IP`, `gamer.player_IP`). Start the server first, then the player
within a second. Both must use the same mode:

```bash
# on the server (10.0.0.1)
cd server && CGREPLAY_CC_FEEDBACK=twcc python3 cg_server1.py      # or rfc8888 / none
# on the player (10.0.0.2)
cd player && CGREPLAY_CC_FEEDBACK=twcc python3 cg_gamer1.py       # same value as the server
```

Use the system Python (GStreamer bindings `python3-gi`). `twcc` needs GStreamer >= 1.20
(`gst-inspect-1.0 rtphdrexttwcc` must succeed); `rfc8888` and `none` work with 1.16+.

**4. Use it from your own code.** The pieces are independent of `cg_server1.py`:

```python
# server: any GStreamer RTP sender with a payloader named "pay" and an encoder named "venc"
from gcc_controller import GCCController, attach_twcc          # server/gcc_controller.py
from rfc8888 import SendTracker, Rfc8888Server                 # server/rfc8888.py

gcc = GCCController(start_kbps=5000, min_kbps=300, max_kbps=6000, log_path="logs/srv_gcc.csv")

# (a) TWCC: payloader must feed rtpbin (rtp-profile=avpf), RTCP from the player into rtpbin.recv_rtcp_sink_0
attach_twcc(Gst, GstRtp, pipeline.get_by_name("pay"), gcc, ext_id=1)

# (b) RFC 8888: plain udpsink pipeline, CCFB from the player on UDP port 5011
tracker = SendTracker()
tracker.attach(Gst, pipeline.get_by_name("pay").get_static_pad("src"))
Rfc8888Server("10.0.0.1", 5011, tracker, gcc).start()

# per frame: combine with your own (application-layer) rate and apply it
rate = int(min(app_rate_kbps, gcc.get_target_kbps()))
pipeline.get_by_name("venc").set_property("bitrate", rate)
```

```python
# player, RFC 8888 only (TWCC feedback is generated by rtpbin, see cg_gamer1.py)
from rfc8888_receiver import Rfc8888Relay                      # player/rfc8888_receiver.py
Rfc8888Relay("10.0.0.2", 5002, 5013, "10.0.0.1", 5011, interval_ms=33).start()
# then decode from udpsrc address=127.0.0.1 port=5013
```

**5. Outputs of a run**

| File | Content |
|------|---------|
| `server/logs/srv_QoEMetrics.csv` | every Ack/Nack/command received: server frame, player frame, sync gap, fps, cps |
| `server/logs/srv_codec_bitrate.csv` | application-layer rate decision per message (jump/rise/decrease/fall) |
| `server/logs/srv_frame.csv` | every frame streamed |
| `server/logs/srv_gcc.csv` | GCC per feedback: acked rate, trend, threshold, signal, delay/loss/target rate |
| `server/logs/srv_rate_combined.csv` | per frame: app-layer rate, GCC rate, rate applied to the encoder |
| `player/logs/ratelog_CG.csv`, `responsetime_CG.csv`, `ply_frame.csv` | player fps/cps, frame-to-command response time, received frames |
| `player/output.pcap` | all UDP between server and player: video (5002), commands (5002 -> 5003), RTCP feedback (-> 5011), RTCP SR (-> 5012) |

In Wireshark: `rtcp.pt == 205 && rtcp.rtpfb.fmt == 15` (TWCC) or `rtcp.rtpfb.fmt == 11` (RFC 8888)
after *Decode As* UDP 5011 -> RTCP; `udp.dstport == 5003` shows the joystick commands.

---

## RTP-frame <-> video-frame dataset

Goal: one row per video frame the server sent, joining what the **server** encoded, what
the **network** carried (RTP headers at several vantage points) and what the **player**
decoded (spatial quality + timing), for every game and transport.

### How RTP packets map to video frames (H.264, also H.265)

One video frame (an *access unit*) is carried by **one or more** RTP packets:

* every packet of a frame has the **same RTP timestamp**; the **last** one has **marker = 1**;
* the sequence number increases by 1 per packet, so gaps = loss, going backwards = reordering;
* the RTP timestamp uses a 90 kHz clock: `server frame rate = 90000 / Δts` between frames.

Packetization (RFC 6184 for H.264, RFC 7798 for H.265; the dataset logs it per packet in
`nal` / `inner` / `fu_start` / `fu_end`):

| Packet carries | H.264 NAL type | H.265 | When |
|---|---|---|---|
| one whole NAL unit | 1 (slice), 5 (IDR), 6 SEI, 7 SPS, 8 PPS, 9 AUD | 0-40 | small slices, parameter sets |
| several small NAL units | 24 STAP-A | 48 AP | SPS+PPS together |
| a fragment of one NAL unit | 28 FU-A (S bit first, E bit last) | 49 FU | any slice bigger than the MTU |

With the default x264 settings (`tune=zerolatency`, 1280x720, GOP 3) an I-frame is
~20-50 KB → **30-40 packets** and a P-frame ~5-10 KB → **5-10 packets**. Frame 1 carries
AUD + SPS + PPS + IDR in separate packets with the same timestamp. So "RTP frame" in
this dataset = *the set of packets sharing one RTP timestamp, ended by the marker bit*,
and it maps 1:1 to a video frame.

RoQ is different. With SRTP (WebRTC) only the payload is encrypted and the 12-byte RTP
header stays in clear, so a network device can read it. In RoQ the whole RTP packet
(header + CGReplay's 16-byte frag header: frame id, index, count, send time + payload) is
the payload of a QUIC DATAGRAM frame, and QUIC encrypts everything after its short header.
In a test capture, 0 of 5,960 RoQ datagrams contained the RTP SSRC bytes. **A network
device cannot read RTP in RoQ**, so the RoQ sender and receiver log the RTP headers
themselves (vantage points `server_tx`, `player_rx`), and the pcaps give QUIC datagram
timing/sizes (`other_packets.csv`). To look inside in Wireshark, the receiver writes
the TLS secrets to `player_logs/quic_keylog.txt`:
`editcap --inject-secrets tls,player_logs/quic_keylog.txt pcap/h2.pcap h2-decrypted.pcapng`
(or Wireshark → Preferences → Protocols → TLS → (Pre)-Master-Secret log filename).
When `tshark`/`editcap` are installed, `tools/build_rtp_dataset.py` does this itself, so
RoQ also gets RTP rows at the network vantage points (`h2`, `s1-eth1`, `s1-eth2`). RoQ frames are all I-frames (GOP 1) and the RTP
payload is raw Annex-B, not RFC 6184 packetization.

### What is logged where

| Vantage point | Source | What |
|---|---|---|
| `server_tx` | `server/rtp_tap.py` (GStreamer: WebRTC/SCReAM), `roq_sender.py` | every RTP packet as it leaves the server + the frame it carries |
| `s1-eth1` | tcpdump on the switch port facing the server | RTP after the server-uplink bottleneck |
| `s1-eth2` | tcpdump on the switch port facing the player | RTP towards the player |
| `h2` | tcpdump on the player (`player/output.pcap`) | RTP + commands + RTCP as the player NIC sees them |
| `player_rx` | `roq_receiver.py` (RoQ only) | every RTP datagram the RoQ receiver got |
| player frames | `cg_gamer1.py` / `roq_receiver.py` → `ply_video_frames.csv` | decode order, receive time, saved PNG |

All Mininet hosts share one clock, so the wall-clock times of all logs and pcaps are
directly comparable (one-way delay = `vp time − server_tx time`).

The server tags each frame it pushes into GStreamer (`GstBuffer.offset`) and reads the
RTP timestamp the payloader gives it, writing `server/logs/srv_video_frames.csv`
(frame id, QR timestamp, push time, RTP timestamp, bitrates). The player side is mapped
offline: each saved PNG carries the server's QR (`Frame ID`, `rcv_timestamp`), which
identifies the exact frame instance, even when the application-layer sync logic
re-streams a frame. `tools/verify_rtp_mapping.py` checks the mapping independently: it
rebuilds every access unit from a pcap, decodes it and compares the QR with the dataset.

### Collect: one run

```bash
sudo python3 topology/simple_topology.py --protocol rtp --cc-feedback twcc --game Kombat \
     --encoder h264 --frames 300 --bw 10 --bn-bw 4 --delay 10ms --skip-metrics
```

Each run lands in `results/<game>_<transport>_<codec>_<bn>M_<date>/`:

```
run.json  config.yaml
server_logs/  srv_rtp.csv  srv_video_frames.csv  srv_QoEMetrics.csv  srv_codec_bitrate.csv
              srv_frame.csv  srv_gcc.csv  srv_rate_combined.csv   (+ srv_roq_*.csv for RoQ)
player_logs/  ply_video_frames.csv  ply_frame.csv  ratelog_CG.csv  responsetime_CG.csv
              (+ ply_rtp.csv, ply_roq_*.csv for RoQ)
pcap/         s1-eth1.pcap  s1-eth2.pcap  h2.pcap
received_frames/  host_logs/
```

Transports: `--protocol rtp --cc-feedback {twcc,rfc8888,none}` (WebRTC-style UDP/RTP),
`--protocol scream`, `--protocol roq`. Games: `--game {Kombat,Forza,Fortnite}` (the frame
folders `server/<Game>/` must be downloaded first). `--no-frames` skips copying the PNGs
(about 1-2 MB each), but then no quality metrics can be computed.

### Collect: every game x transport

```bash
sudo tools/run_matrix.sh
# knobs (environment): GAMES, TRANSPORTS, ENCODER, FRAMES, BW, BN_BW, DELAY, JITTER, LOSS, REPEAT
sudo GAMES="Kombat Forza Fortnite" TRANSPORTS="webrtc-twcc webrtc-rfc8888 webrtc-none scream roq" \
     BN_BW=4 DELAY=20ms REPEAT=3 tools/run_matrix.sh
```

It runs each combination, collects it, and finally builds the merged dataset
`results/dataset_<date>.csv` (using `~/venv/bin/python3`; override with `PY_TOOLS`).

### Merge into the dataset

```bash
python3 tools/build_rtp_dataset.py results/<run_id> [more runs...] --combined results/all.csv
python3 tools/verify_rtp_mapping.py results/<run_id>          # optional mapping check (not RoQ)
```

Needs `pandas opencv-python pyzbar scikit-image` (+ `av` for the verifier, `tshark` to decrypt RoQ pcaps). VMAF needs an
ffmpeg built with libvmaf (`CGREPLAY_FFMPEG=/path/to/ffmpeg`, otherwise skipped). LPIPS
needs `pip install lpips torch` (otherwise skipped). `--no-vmaf` / `--no-lpips` turn them off.

Outputs in `results/<run_id>/dataset/`:

| File | One row per | Main columns |
|---|---|---|
| `rtp_packets.csv` | RTP packet × vantage point | `vp, wall, seq, ext_seq, rtp_ts, marker, pt, ssrc, pkt_len, payload_len, ext, nal, inner, fu_start, fu_end, slice, keyframe, reordered, duplicate, jitter_ms, frame_id, instance` |
| `rtp_frames.csv` | RTP frame × vantage point | `pkts, pkts_expected, lost_pkts, complete, reordered_pkts, rtp_bytes, spread_ms, owd_first_ms, owd_last_ms, jitter_ms, iat_ms, fps_arrival, rtp_ts_delta, fps_rtp_ts, kbps_1s, frame_type, nal_types` |
| `video_frames.csv` | video frame sent by the server | run info (game, transport, codec, link) + server (`frame_id, instance, frame_type, push_wall, server_fps_push, server_fps_rtp, app_bitrate, applied_bitrate`) + `<vp>_*` RTP-frame features for every vantage point + player (`player_decoded, player_decode_idx, player_recv_wall, player_fps, e2e_latency_ms, player_map_method`) + `PSNR, SSIM, VMAF, LPIPS` |
| `player_frames.csv` | decoded frame | decode order, receive time, mapped instance, mapping method (`qr`, `interp`, `frame_header`) |
| `commands.csv` | Ack/Nack/command message × vantage point | type, frame id, fps, cps |
| `other_packets.csv` | non-RTP UDP × vantage point | RTCP (pt/fmt: 205/15 TWCC, 205/11 RFC 8888, 200 SR, 201 RR), QUIC |

Quality is measured against the original game frame (`server/<Game>/<frame_id>.png`,
resized as the server does), with the QR corner masked identically in both images.
Frames that never decoded at the player have empty quality columns. Their network rows
(`h2_lost_pkts > 0`, `h2_complete = 0`) are usually the reason.

## QoE metric

Perceived quality combines video quality and interaction latency:

```
QoE(t) = (VMAF(t) / 100) * exp(-RT(t) / tau)
```

where `RT` is the command response time in milliseconds and `tau` is a latency
sensitivity constant. The formula lives in `tools/qoe.py` as the single source of
truth for both the post-processing pipeline and the dashboard.

---

## Citation

```
[1] Shirmarz, A., de Castro, A. G., Verdi, F. L., & Rothenberg, C. E. (2025).
CGReplay: Capture and Replay of Cloud Gaming Traffic for QoE/QoS Assessment.
arXiv preprint arXiv:2505.11973.

```
