#!/bin/bash
# CGReplay-compatible SCReAM sender.
# Prints a GStreamer pipeline string to stdout; called by cg_server1.py.
# The pipeline uses appsrc so CGReplay can push OpenCV frames into it.

SCRIPT_DIR=$(dirname "$(realpath "$0")")
SCREAMLIB="$SCRIPT_DIR/../../code/wrapper_lib"
export GST_PLUGIN_PATH="$SCRIPT_DIR/../target/debug/:${GST_PLUGIN_PATH:-}"
export LD_LIBRARY_PATH="$SCREAMLIB:${LD_LIBRARY_PATH:-}"

PLAYER_IP="${CGREPLAY_PLAYER_IP:-10.0.0.2}"
RTP_PORT="${CGREPLAY_RTP_PORT:-5002}"
BIND_PORT="${CGREPLAY_BIND_PORT:-5000}"
WIDTH="${CGREPLAY_WIDTH:-1280}"
HEIGHT="${CGREPLAY_HEIGHT:-720}"
FPS="${CGREPLAY_FPS:-30}"
BITRATE="${CGREPLAY_BITRATE:-2500}"
GOP="${CGREPLAY_GOP:-3}"
ENCODER="${CGREPLAY_ENCODER:-h264}"
# SCReAM rate band. maxrate must give headroom over the encoder's bursty I-frame
# peaks (key-int-max small -> frequent I-frames) so the RTP queue drains instead
# of building delay and discarding. On a clean 10Mbps link 8000 leaves room;
# SCReAM still backs off on real congestion. initrate starts mid-band, not at max.
INITRATE="${CGREPLAY_INITRATE:-5000}"
MINRATE="${CGREPLAY_MINRATE:-2000}"
MAXRATE="${CGREPLAY_MAXRATE:-8000}"

# Pick encoder / parser / RTP payloader for the requested codec.
# x265enc emits VPS/SPS/PPS only once by default, so a receiver that joins
# mid-stream (the player starts after the server) never gets the parameter sets
# and decodes nothing. repeat-headers=1 puts them on every IDR. x264enc already
# repeats SPS/PPS, so it needs no extra option.
if [ "$ENCODER" = "h265" ] || [ "$ENCODER" = "H.265" ] || [ "$ENCODER" = "hevc" ]; then
    ENC="x265enc"; PARSE="h265parse"; PAY="rtph265pay"; ENCOPTS="option-string=\"repeat-headers=1\""
else
    ENC="x264enc"; PARSE="h264parse"; PAY="rtph264pay"; ENCOPTS=""
fi

echo "appsrc name=source is-live=true block=true format=GST_FORMAT_TIME do-timestamp=true ! \
videoconvert ! video/x-raw,format=I420,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1 ! \
${ENC} name=encoder0 bitrate=${BITRATE} speed-preset=ultrafast tune=zerolatency key-int-max=${GOP} ${ENCOPTS} ! \
${PARSE} ! ${PAY} config-interval=-1 ! \
screamtx name=screamtx0 params=\"-initrate ${INITRATE} -minrate ${MINRATE} -maxrate ${MAXRATE}\" ! \
udpsink host=${PLAYER_IP} port=${RTP_PORT} bind-port=${BIND_PORT}"
