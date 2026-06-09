#!/bin/bash
# CGReplay SCReAM receiver.
# screamtx does NOT modify RTP packet format — standard RTP pipeline works.
# screamrx omitted: without a feedback UDP channel, it blocks the pipeline.

RTP_PORT="${CGREPLAY_RTP_PORT:-5002}"
ENCODER="${CGREPLAY_ENCODER:-h264}"

if [ "$ENCODER" = "h265" ] || [ "$ENCODER" = "H.265" ] || [ "$ENCODER" = "hevc" ]; then
    DEPAY="rtph265depay"; DEC="avdec_h265"
else
    DEPAY="rtph264depay"; DEC="avdec_h264"
fi

echo "udpsrc port=${RTP_PORT} timeout=3000000000 ! application/x-rtp,payload=96 ! \
queue max-size-time=1000000000 ! \
${DEPAY} ! ${DEC} ! videoconvert ! appsink"
