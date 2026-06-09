#!/usr/bin/env python3
"""
Perceived-quality (QoE) model for CGReplay.

QoE combines visual quality (VMAF) with interaction latency (response time):
higher VMAF raises it, higher response time lowers it. This is the single
source of truth — both post_process.py (writes the QoE column) and gui_app.py
(plots it) call perceived_qoe(), so the formula lives in one place.

PLACEHOLDER FORMULA — Alireza will provide the exact one ("qoe = vmaf and
response time ..."). Until then:

    QoE = (VMAF / 100) * exp(-RT_ms / TAU)        in [0, 1]

  - VMAF/100 normalises visual quality to [0, 1].
  - exp(-RT/TAU) is a latency penalty: 1.0 at 0 ms, ~0.61 at TAU, decaying
    toward 0. TAU=150 ms reflects the rough tolerance band for cloud gaming.

To swap in Alireza's formula, edit ONLY this function.
"""

import numpy as np

TAU_MS = 150.0  # response-time tolerance constant (ms)


def perceived_qoe(vmaf, rt_ms):
    """Per-frame QoE from VMAF (0–100) and response time (ms).

    Accepts scalars or pandas/numpy arrays. Missing response time is treated as
    no latency penalty (penalty=1) so frames without a command still get a QoE
    from their VMAF.
    """
    v = np.asarray(vmaf, dtype=float) / 100.0
    rt = np.asarray(rt_ms, dtype=float)
    penalty = np.where(np.isnan(rt), 1.0, np.exp(-np.clip(rt, 0, None) / TAU_MS))
    return v * penalty
