import cv2
import os
import subprocess
import csv
from skimage.metrics import structural_similarity as _skimage_ssim

# System ffmpeg (4.4 here) is built without libvmaf. Use the static build that
# includes it for VMAF + the temp mp4 encoding. Override with CGREPLAY_FFMPEG.
_STATIC_FFMPEG = "/home/hugo/CGSynth/tools/ffmpeg-vmaf/ffmpeg"
FFMPEG = os.environ.get(
    "CGREPLAY_FFMPEG",
    _STATIC_FFMPEG if os.path.exists(_STATIC_FFMPEG) else "ffmpeg",
)

def ssim(img1, img2):
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    return _skimage_ssim(gray1, gray2, data_range=255)

def psnr(img1, img2):
    return cv2.PSNR(img1, img2)

def vmaf_score(ref_path, tgt_path):
    cmd = [
        FFMPEG, "-i", tgt_path, "-i", ref_path,
        "-lavfi", "[0:v][1:v]libvmaf", "-f", "null", "-"
    ]
    result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
    for line in result.stderr.splitlines():
        if "VMAF score" in line:
            try:
                return float(line.split(":")[-1].strip())
            except ValueError:
                return None
    return None

def mask_qr_with_ref(img_ref, img_tgt, qr_size=200, padding=10):
    h, w = img_ref.shape[:2]
    img_tgt_masked = img_tgt.copy()
    # Replace QR region in target with reference pixels
    img_tgt_masked[h-qr_size-padding:h-padding, w-qr_size-padding:w-padding] = img_ref[h-qr_size-padding:h-padding, w-qr_size-padding:w-padding]
    return img_tgt_masked


def mask_qr(img, qr_size=200, padding=10):
    h, w = img.shape[:2]
    img_masked = img.copy()
    img_masked[h-qr_size-padding:h-padding, w-qr_size-padding:w-padding] = 0
    return img_masked

def compare_images(ref_folder, tgt_folder, start_num, end_num, csv_path, qr_size=200, padding=10):
    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["frame", "SSIM", "PSNR", "VMAF"])
        for i in range(start_num, end_num + 1):
            fname = f"{i:04d}.png"
            ref_img_path = os.path.join(ref_folder, fname)
            tgt_img_path = os.path.join(tgt_folder, fname)
            print(f"Processing {fname}...")
            if not os.path.exists(ref_img_path) or not os.path.exists(tgt_img_path):
                print(f"Missing: {ref_img_path} or {tgt_img_path}")
                continue
            img1 = cv2.imread(ref_img_path)
            img2 = cv2.imread(tgt_img_path)
            if img1 is None or img2 is None:
                print(f"Image load error for {fname}")
                continue
            if img1.shape != img2.shape:
                img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
            # Neutralize the QR region CONSISTENTLY in both images. The reference
            # (Kombat) is clean (no QR); the received frame has a QR burned in for
            # sync. Earlier this zeroed the reference corner but ref-filled the
            # target corner -> a constant ~200px mismatch that capped PSNR at ~19dB
            # regardless of bitrate. Fix: leave the clean reference as-is and copy
            # its corner into the target, so both match in the QR region (same
            # approach used for VMAF in post_process.compute_vmaf_sequence).
            img1_masked = img1
            img2_masked = mask_qr_with_ref(img1, img2, qr_size=qr_size, padding=padding)
            ssim_val = ssim(img1_masked, img2_masked)
            psnr_val = psnr(img1_masked, img2_masked)
            # VMAF is computed separately over the whole sequence (one libvmaf
            # pass with temporal context) in post_process.compute_vmaf_sequence,
            # which is far faster and more meaningful than per-frame VMAF.
            writer.writerow([fname, ssim_val, psnr_val, ""])

if __name__ == "__main__":
    compare_images(
        "/home/alireza/mycg/CGReplay/server/Kombat",
        "/home/alireza/mycg/CGReplay/player/received_frames",
        2, 101,
        "/home/alireza/mycg/CGReplay/tools/VQ/videoQ.csv"
    )