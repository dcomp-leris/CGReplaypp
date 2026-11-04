#!/bin/bash
# CGReplay++ — run every game x transport combination in Mininet, collect each run into
# results/<run_id>/ and merge them into the RTP-frame <-> video-frame dataset.
#
#   sudo tools/run_matrix.sh                                   # defaults below
#   sudo GAMES="Kombat" TRANSPORTS="webrtc-twcc roq" BN_BW=3 tools/run_matrix.sh
#
# Transports: webrtc-twcc | webrtc-rfc8888 | webrtc-none  (UDP/RTP, GStreamer)
#             scream                                      (needs the Ericsson gstscream build)
#             roq                                         (RTP over QUIC datagrams, venv)
# Every knob is an environment variable:
GAMES="${GAMES:-Kombat Forza Fortnite}"
TRANSPORTS="${TRANSPORTS:-webrtc-twcc webrtc-rfc8888 webrtc-none scream roq}"
ENCODER="${ENCODER:-h264}"
FRAMES="${FRAMES:-300}"
BW="${BW:-10}"              # access link (Mbps)
BN_BW="${BN_BW:-4}"         # server-uplink bottleneck (Mbps)
DELAY="${DELAY:-10ms}"
JITTER="${JITTER:-0ms}"
LOSS="${LOSS:-0}"
REPEAT="${REPEAT:-1}"
RESULTS="${RESULTS:-results}"
NO_FRAMES="${NO_FRAMES:-0}"       # 1 = do not keep received PNGs (no quality metrics then)
PY_TOOLS="${PY_TOOLS:-/home/${SUDO_USER:-$USER}/venv/bin/python3}"   # pandas, opencv, pyzbar, skimage

cd "$(dirname "$(realpath "$0")")/.." || exit 1
[ "$(id -u)" = 0 ] || { echo "run with sudo (Mininet needs root)"; exit 1; }
mkdir -p "$RESULTS"
STAMP=$(date +%Y%m%d-%H%M%S)
LIST="$RESULTS/matrix_${STAMP}.txt"; : > "$LIST"

for rep in $(seq 1 "$REPEAT"); do
  for game in $GAMES; do
    if [ ! -d "server/$game" ]; then echo "[skip] server/$game missing (download the dataset)"; continue; fi
    for tr in $TRANSPORTS; do
      case $tr in
        webrtc-*) PROTO_ARGS="--protocol rtp --cc-feedback ${tr#webrtc-}";;
        scream)   PROTO_ARGS="--protocol scream";;
        roq)      PROTO_ARGS="--protocol roq";;
        *) echo "[skip] unknown transport $tr"; continue;;
      esac
      echo "=== [$rep/$REPEAT] game=$game transport=$tr"
      before=$(ls -1d "$RESULTS"/*/ 2>/dev/null | sort)
      mn -c >/dev/null 2>&1
      extra=""; [ "$NO_FRAMES" = 1 ] && extra="--no-frames"
      python3 topology/simple_topology.py $PROTO_ARGS --game "$game" --encoder "$ENCODER" \
          --frames "$FRAMES" --bw "$BW" --bn-bw "$BN_BW" --delay "$DELAY" --jitter "$JITTER" \
          --loss "$LOSS" --results-dir "$RESULTS" --skip-metrics $extra
      after=$(ls -1d "$RESULTS"/*/ 2>/dev/null | sort)
      comm -13 <(echo "$before") <(echo "$after") >> "$LIST"
    done
  done
done

echo "=== runs:"; cat "$LIST"
if [ -s "$LIST" ]; then
  sudo -u "${SUDO_USER:-root}" "$PY_TOOLS" tools/build_rtp_dataset.py $(cat "$LIST") \
      --combined "$RESULTS/dataset_${STAMP}.csv"
  echo "=== combined dataset: $RESULTS/dataset_${STAMP}.csv"
fi
