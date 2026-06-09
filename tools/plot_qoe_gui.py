#!/usr/bin/env python3
"""
CGReplay interactive QoE dashboard.

Run from CGReplay/ root after post_process.py for each mode:
    python3 tools/plot_qoe_gui.py

Outputs:
    player/logs/dashboard.html  — open in any browser (no server needed)
"""

import os
import sys
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

LOGS_DIR = os.path.join("player", "logs")
MODES    = ["quic", "rtp", "scream"]

COLORS  = {"quic": "#1f77b4", "rtp": "#e07b00", "scream": "#2ca02c"}
LABELS  = {"quic": "QUIC",    "rtp": "Pure UDP (RTP)", "scream": "SCReAM"}
MARKERS = {"quic": "circle",  "rtp": "square",         "scream": "diamond"}
DASHES  = {"quic": "solid",   "rtp": "dash",           "scream": "dot"}

METRICS = [
    ("SSIM",              "SSIM",             "Video Quality (SSIM)", (0, 1)),
    ("PSNR",              "PSNR (dB)",        "PSNR",                 None),
    ("response_time_ms",  "Response Time (ms)", "Response Time",      None),
    ("QoE",               "QoE",              "QoE  [SSIM × FPS/FPS_target]", (0, 1)),
]

AXIS_FONT  = dict(size=15, family="Arial Black", color="#222")
TICK_FONT  = dict(size=13, family="Arial", color="#333")
TITLE_FONT = dict(size=14, family="Arial Black", color="#111")


def load(mode: str) -> pd.DataFrame | None:
    path = os.path.join(LOGS_DIR, f"metrics_{mode}.csv")
    if not os.path.exists(path):
        print(f"  [skip] {path} not found")
        return None
    return pd.read_csv(path)


def build_dashboard():
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=[m[2] for m in METRICS],
        horizontal_spacing=0.13,
        vertical_spacing=0.18,
    )

    positions = [(1, 1), (1, 2), (2, 1), (2, 2)]
    legend_added = set()

    for mode in MODES:
        df = load(mode)
        if df is None or "frame_id" not in df.columns:
            continue

        for (row, col), (metric, ylabel, _, ylim) in zip(positions, METRICS):
            if metric not in df.columns:
                continue

            show_legend = mode not in legend_added
            if show_legend:
                legend_added.add(mode)

            # Response time is event-based (only at command frames) — show as
            # markers; the continuous metrics use lines+markers.
            trace_mode = "markers" if metric == "response_time_ms" else "lines+markers"

            fig.add_trace(
                go.Scatter(
                    x=df["frame_id"],
                    y=df[metric],
                    mode=trace_mode,
                    name=LABELS[mode],
                    line=dict(
                        color=COLORS[mode],
                        width=2.5,
                        dash=DASHES[mode],
                    ),
                    marker=dict(
                        symbol=MARKERS[mode],
                        size=6,
                        color=COLORS[mode],
                        line=dict(width=1, color="white"),
                    ),
                    legendgroup=mode,
                    showlegend=show_legend,
                    hovertemplate=(
                        f"<b>{LABELS[mode]}</b><br>"
                        f"frame = %{{x}}<br>"
                        f"{ylabel} = %{{y:.3f}}"
                        f"<extra></extra>"
                    ),
                    connectgaps=False,
                ),
                row=row, col=col,
            )

    # Global layout
    fig.update_layout(
        title=dict(
            text="<b>CGReplay — Transport Mode Comparison</b>",
            font=dict(size=22, family="Arial Black", color="#111"),
            x=0.5,
            xanchor="center",
        ),
        plot_bgcolor="white",
        paper_bgcolor="#f8f8f8",
        legend=dict(
            font=dict(size=14, family="Arial"),
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#ccc",
            borderwidth=1,
            orientation="h",
            x=0.5, xanchor="center",
            y=-0.07,
        ),
        margin=dict(l=65, r=30, t=90, b=80),
        width=1100,
        height=720,
        hovermode="x unified",
    )

    # Per-subplot axis styling
    for (row, col), (metric, ylabel, _, ylim) in zip(positions, METRICS):
        fig.update_xaxes(
            title_text="<b>Frame</b>",
            title_font=AXIS_FONT,
            tickfont=TICK_FONT,
            showgrid=True,
            gridcolor="#e4e4e4",
            gridwidth=1,
            dtick=20,
            zeroline=False,
            row=row, col=col,
        )
        fig.update_yaxes(
            title_text=f"<b>{ylabel}</b>",
            title_font=AXIS_FONT,
            tickfont=TICK_FONT,
            showgrid=True,
            gridcolor="#e4e4e4",
            gridwidth=1,
            zeroline=False,
            row=row, col=col,
        )
        if ylim is not None:
            fig.update_yaxes(range=list(ylim), row=row, col=col)

    # Bold subplot titles
    for ann in fig.layout.annotations:
        ann.font = TITLE_FONT
        if not ann.text.startswith("<b>"):
            ann.text = f"<b>{ann.text}</b>"

    out = os.path.join(LOGS_DIR, "dashboard.html")
    fig.write_html(out, include_plotlyjs="cdn", full_html=True)
    print(f"[plot_qoe_gui] Saved: {out}")
    print(f"               Open:  firefox {out}  (or xdg-open {out})")


if __name__ == "__main__":
    print("[plot_qoe_gui] Building interactive dashboard ...")
    build_dashboard()
    print("[plot_qoe_gui] Done.")
