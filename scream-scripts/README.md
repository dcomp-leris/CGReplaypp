# SCReAM scripts (CGReplay++ customized)

`cg_server1.py` / the player call these to print the GStreamer pipelines for the
SCReAM transport. They are CGReplay++ versions of the scripts shipped with
[EricssonResearch/scream](https://github.com/EricssonResearch/scream), with:

- **appsrc** input so CGReplay can push OpenCV frames into the pipeline
- **encoder-aware** codec selection (H.264 / H.265 via `CGREPLAY_ENCODER`); H.265
  adds `repeat-headers=1` so the late-joining player gets VPS/SPS/PPS
- **tuned SCReAM rate band** in `sender.sh`: `-initrate 5000 -minrate 2000
  -maxrate 8000` and encoder `bitrate=2500`. maxrate gives headroom over the
  bursty I-frames so the RTP queue drains instead of discarding; initrate (2x the
  encoder rate) drains the startup backlog on a clean link. SCReAM still backs off
  on real congestion. All values are env-overridable (`CGREPLAY_BITRATE`,
  `CGREPLAY_INITRATE`, `CGREPLAY_MINRATE`, `CGREPLAY_MAXRATE`, `CGREPLAY_GOP`).
- `receiver.sh` omits `screamrx` (no feedback UDP channel is wired, so it would
  block the pipeline); standard RTP depay/decode works because screamtx does not
  change the RTP packet format.

## Install

After cloning and building the Ericsson scream repo (see the main README, step 6),
copy these over its scripts:

```bash
cp scream-scripts/sender.sh scream-scripts/receiver.sh \
   ~/CGSynth/scream/gstscream/scripts/
```
