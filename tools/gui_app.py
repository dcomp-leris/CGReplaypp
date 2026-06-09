#!/usr/bin/env python3
"""
CGReplay — interactive control panel (the "figure" for the framework + GUI).

Two parts, per Alireza's spec:

  1. Configuration
       (a) network: bandwidth, packet loss, delay
       (b) encoder: H.264 / H.265
       (c) transport protocol: UDP / QUIC / SCReAM
       (d) run duration (seconds)

  2. Start → runs that configuration end-to-end (Mininet topology + metrics),
     then shows the QoE plots:
       (a) VMAF  (b) SSIM  (c) PSNR  (d) Response Time  (e) Perceived Quality (QoE)

"Interactive" = set a configuration, click Start, watch it run, see the plots.
Already-run protocols stay on the charts so you can compare them.

Run (must be root so it can drive Mininet without a sudo password prompt):
    sudo -E /home/hugo/venv/bin/streamlit run tools/gui_app.py

Then open the URL Streamlit prints (http://localhost:8501).
"""

import os
import subprocess

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------
ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(ROOT, "player", "logs")
TOPOLOGY = os.path.join(ROOT, "topology", "simple_topology.py")
SYS_PY   = "/usr/bin/python3"   # Mininet lives in the system interpreter

with open(os.path.join(ROOT, "config", "config.yaml")) as _f:
    _cfg = yaml.safe_load(_f)
FPS_TARGET = _cfg["encoding"]["fps"]

# protocol value used on the CLI  ->  display label / colour / line style
MODES    = ["rtp", "quic", "scream"]
LABELS   = {"rtp": "UDP", "quic": "QUIC", "scream": "SCReAM"}
COLORS   = {"rtp": "#e07b00", "quic": "#1f77b4", "scream": "#2ca02c"}
DASHES   = {"rtp": "dash", "quic": "solid", "scream": "dot"}
MARKERS  = {"rtp": "square", "quic": "circle", "scream": "diamond"}
PROTO_UI = {"UDP": "rtp", "QUIC": "quic", "SCReAM": "scream"}
ENC_UI   = {"H.264": "h264", "H.265": "h265"}

# (column, label, y-range) — the QoE plots Alireza asked for
METRICS = [
    ("VMAF",             "VMAF (0–100)",            (0, 100)),
    ("SSIM",             "SSIM (0–1)",              (0, 1)),
    ("PSNR",             "PSNR (dB)",               None),
    ("response_time_ms", "Response Time (ms)",      None),
    ("QoE",              "Perceived Quality (QoE)", (0, 1)),
]

AXIS_FONT = dict(size=16, family="Arial Black", color="#1a1a1a")
TICK_FONT = dict(size=13, family="Arial", color="#333")


# ---------------------------------------------------------------------------
# Data + runner
# ---------------------------------------------------------------------------
@st.cache_data
def load(mode: str):
    path = os.path.join(LOGS_DIR, f"metrics_{mode}.csv")
    if not os.path.exists(path):
        return None
    return pd.read_csv(path)


def run_config(protocol, encoder, bw, delay_ms, loss, frames, log_box):
    """Run one full configuration through the topology + post-processing.

    Streams the topology's stdout into `log_box` live. Returns the exit code.
    Must be root (Mininet); the whole app is launched with sudo so no password
    prompt appears mid-run.
    """
    cmd = [SYS_PY, TOPOLOGY,
           "--protocol", protocol, "--encoder", encoder,
           "--bw", str(bw), "--delay", f"{delay_ms}ms",
           "--loss", str(loss), "--frames", str(frames)]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd

    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")        # live game window inside Mininet
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd, cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    lines: list[str] = []
    for line in proc.stdout:
        lines.append(line.rstrip())
        log_box.code("\n".join(lines[-40:]) or "…", language="text")
    proc.wait()
    return proc.returncode


def metric_figure(col, ylabel, ylim, modes_on):
    """Styled Plotly figure for one metric across the selected protocols."""
    fig = go.Figure()
    scatter = (col == "response_time_ms")  # event-based → markers only
    for mode in modes_on:
        df = load(mode)
        if df is None or col not in df.columns or "frame_id" not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=df["frame_id"], y=df[col],
            mode="markers" if scatter else "lines+markers",
            name=LABELS[mode],
            line=dict(color=COLORS[mode], width=2.6, dash=DASHES[mode]),
            marker=dict(symbol=MARKERS[mode], size=7 if scatter else 6,
                        color=COLORS[mode], line=dict(width=1, color="white")),
            connectgaps=False,
            hovertemplate=f"<b>{LABELS[mode]}</b><br>frame %{{x}}<br>"
                          f"{ylabel}: %{{y:.3f}}<extra></extra>",
        ))
    fig.update_layout(
        margin=dict(l=70, r=20, t=10, b=50), height=330,
        plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(orientation="h", x=0.5, xanchor="center", y=1.12,
                    font=dict(size=13)),
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="<b>Frame</b>", title_font=AXIS_FONT,
                     tickfont=TICK_FONT, showgrid=True, gridcolor="#e6e6e6",
                     dtick=20, zeroline=False)
    fig.update_yaxes(title_text=f"<b>{ylabel}</b>", title_font=AXIS_FONT,
                     tickfont=TICK_FONT, showgrid=True, gridcolor="#e6e6e6",
                     zeroline=False)
    if ylim:
        fig.update_yaxes(range=list(ylim))
    return fig


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.set_page_config(page_title="CGReplay — Control Panel", layout="wide")
st.title("CGReplay — Cloud Gaming Transport Control Panel")
st.caption("Configure the network, encoder, protocol and duration, then run "
           "and compare the QoE plots.")

is_root = (os.geteuid() == 0)
if not is_root:
    st.warning(
        "Start is disabled: launch the app as root so it can drive Mininet "
        "without a password prompt:\n\n"
        "`sudo -E /home/hugo/venv/bin/streamlit run tools/gui_app.py`",
        icon="⚠️",
    )

# ===== 1 · Configuration ====================================================
st.subheader("1 · Configuration")

st.markdown("**Network condition**")
nc1, nc2, nc3 = st.columns(3)
bw    = nc1.number_input("Bandwidth (Mbps)", 0.1, 1000.0, 10.0, step=1.0)
delay = nc2.number_input("Delay (ms)",       0.0, 1000.0, 10.0, step=1.0)
loss  = nc3.number_input("Packet loss (%)",  0.0,  100.0,  0.0, step=0.5)

cc1, cc2, cc3 = st.columns(3)
encoder_ui  = cc1.radio("Encoder", list(ENC_UI), horizontal=True)
protocol_ui = cc2.radio("Transport protocol", list(PROTO_UI), horizontal=True)
duration    = cc3.number_input("Run duration (s)", 1.0, 120.0, 4.0, step=1.0)

encoder  = ENC_UI[encoder_ui]
protocol = PROTO_UI[protocol_ui]
frames   = int(round(duration * FPS_TARGET)) + 1   # loop streams 1..frames-1
st.caption(f"≈ {frames - 1} frames at {FPS_TARGET} fps")

# ===== 2 · Run + plots ======================================================
st.subheader("2 · Run")
start = st.button(f"▶ Start  ({protocol_ui} · {encoder_ui} · {bw:g} Mbps · "
                  f"{delay:g} ms · {loss:g}% loss · {duration:g} s)",
                  type="primary", width="stretch", disabled=not is_root)

if start:
    log_box = st.empty()
    with st.spinner(f"Running {protocol_ui} / {encoder_ui} … "
                    "(Mininet test + metrics)"):
        rc = run_config(protocol, encoder, bw, delay, loss, frames, log_box)
    if rc == 0:
        load.clear()
        st.success(f"{protocol_ui} / {encoder_ui} finished. Plots updated below.")
    else:
        st.error(f"Run exited with code {rc}. See the log above.")

st.subheader("3 · QoE plots")
available = [m for m in MODES if load(m) is not None]
if not available:
    st.info("No results yet. Configure above and click Start.")
    st.stop()

st.sidebar.header("Show on charts")
modes_on = [m for m in MODES
            if m in available
            and st.sidebar.checkbox(LABELS[m], value=True, key=f"show_{m}")]
if st.sidebar.button("↻ Reload data"):
    load.clear()
    st.rerun()
st.sidebar.caption("QoE = f(VMAF, response time) — see tools/qoe.py "
                   "(placeholder until Alireza's exact formula).")

# Summary — averages per protocol
rows = []
for mode in modes_on:
    df = load(mode)
    rows.append({
        "Protocol": LABELS[mode],
        "Frames received": int(df["SSIM"].notna().sum()),
        "VMAF": round(df["VMAF"].mean(), 2) if "VMAF" in df else float("nan"),
        "SSIM": round(df["SSIM"].mean(), 3),
        "PSNR (dB)": round(df["PSNR"].mean(), 2),
        "RT (ms)": round(df["response_time_ms"].mean(), 1),
        "QoE": round(df["QoE"].mean(), 3),
    })
if rows:
    st.dataframe(pd.DataFrame(rows).set_index("Protocol"), width="stretch")

# One chart per metric, two per row
for i in range(0, len(METRICS), 2):
    cols = st.columns(2)
    for col_box, (mcol, mlabel, mlim) in zip(cols, METRICS[i:i + 2]):
        with col_box:
            st.markdown(f"**{mlabel}**")
            st.plotly_chart(metric_figure(mcol, mlabel, mlim, modes_on),
                            width="stretch")
