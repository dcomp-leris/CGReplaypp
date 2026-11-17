#!/usr/bin/env bash
# One-shot setup of the uv-managed environment for CGReplay++.
# Creates .venv (with --system-site-packages so GStreamer/gi/opencv come from the system)
# and installs the numpy-1 Python deps that aren't apt-provided. See pyproject.toml.
#
#   ./setup.sh        # create/refresh .venv and install deps
#
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null 2>&1 || { echo "uv not found. Install it: python3 -m pip install --user uv"; exit 1; }

echo "[setup] creating .venv (--system-site-packages) ..."
uv venv --system-site-packages .venv

echo "[setup] installing deps (transports + merge + demo UI) ..."
uv pip install --python .venv/bin/python \
  "numpy<2" aioquic "av<14" "scikit-image<0.22" \
  "fastapi>=0.110.0" "uvicorn[standard]>=0.29.0" streamlit matplotlib

echo "[setup] verifying imports (transports + merge + GStreamer) ..."
.venv/bin/python - <<'PY'
import numpy, cv2, gi, skimage, av, aioquic, pandas, qrcode, pyzbar, yaml
gi.require_version("Gst", "1.0"); from gi.repository import Gst
gst = [l for l in cv2.getBuildInformation().splitlines() if "GStreamer" in l][0].strip()
assert "YES" in gst, "opencv has no GStreamer (need the system build): " + gst
from skimage.metrics import structural_similarity  # SSIM used by the merge
print(f"OK  numpy {numpy.__version__}  cv2 {cv2.__version__} ({gst})  "
      f"skimage {skimage.__version__}  av {av.__version__}  aioquic {aioquic.__version__}")
PY
echo "[setup] done. Run transports with: cc-lab/run_one.sh <transport> <game> <scenario>  (uses .venv)"
