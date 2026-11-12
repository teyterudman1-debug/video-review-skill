#!/usr/bin/env python3
"""Make a review copy of a video that fits the artifact asset limit (20 MiB).

    python3 review_copy.py <input video> [--out review.mp4] [--max-mb 18]

Prints one JSON object: {"path", "duration", "w", "h", "fps", "size_mb", "reencoded"}.
Those values go straight into the `videos` row for the review page.

The source file is never touched. The frame rate is kept, so a timecode on the
review copy is the same frame in the source. Needs ffmpeg and ffprobe on PATH.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

AUDIO_K = 96


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        raise SystemExit("No video stream found in " + path)
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    if not fps or fps > 240:
        fps = 30.0
    w, h = int(v["width"]), int(v["height"])
    # phone footage is often stored sideways with a rotation flag
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
    if abs(rot) % 180 == 90:
        w, h = h, w
    return {
        "duration": float(info["format"].get("duration") or v.get("duration") or 0),
        "w": w, "h": h, "fps": fps, "codec": v.get("codec_name", ""),
        "pix_fmt": v.get("pix_fmt", ""), "has_audio": has_audio,
        "container": info["format"].get("format_name", ""),
    }


def even(n):
    n = int(round(n))
    return n - (n % 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", help="output path (default: <input name>-review.mp4 beside the input)")
    ap.add_argument("--max-mb", type=float, default=18.0, help="size budget in MiB; the hard limit is 20")
    a = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(tool + " is not installed. Install ffmpeg, or upload an MP4 under 20 MB through the page's own 'Add a cut' button.")
    if not os.path.isfile(a.input):
        raise SystemExit("No such file: " + a.input)

    src = probe(a.input)
    if src["duration"] <= 0:
        raise SystemExit("Could not read the video's duration.")
    out = a.out or os.path.splitext(a.input)[0] + "-review.mp4"
    budget = a.max_mb * 1024 * 1024
    size = os.path.getsize(a.input)
    fps_i = max(1, int(round(src["fps"])))

    already_fine = (size <= budget and src["codec"] == "h264" and src["pix_fmt"] in ("yuv420p", "yuvj420p")
                    and "mp4" in src["container"])
    if already_fine:
        # same picture, just make sure the index is at the front so it starts playing at once
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.input, "-c", "copy", "-movflags", "+faststart", out], check=True)
        w, h, reenc = src["w"], src["h"], False
    else:
        audio_bps = AUDIO_K * 1000 if src["has_audio"] else 0
        factor, reenc = 0.94, True
        while True:
            v_bps = budget * 8 * factor / src["duration"] - audio_bps
            v_k = int(min(2500, v_bps / 1000))
            if v_k < 120:
                raise SystemExit("This video is too long to fit in 20 MB at a watchable quality (%.0f min). Split it into parts and review each part as its own cut." % (src["duration"] / 60))
            long_edge = 1280 if v_k >= 700 else 960 if v_k >= 350 else 640
            scale = min(1.0, long_edge / max(src["w"], src["h"]))
            w, h = even(src["w"] * scale), even(src["h"] * scale)
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", a.input, "-vf", "scale=%d:%d" % (w, h),
                   "-c:v", "libx264", "-preset", "medium", "-b:v", "%dk" % v_k, "-maxrate", "%dk" % int(v_k * 1.5),
                   "-bufsize", "%dk" % (v_k * 2), "-g", str(fps_i), "-pix_fmt", "yuv420p"]
            cmd += ["-c:a", "aac", "-b:a", "%dk" % AUDIO_K] if src["has_audio"] else ["-an"]
            cmd += ["-movflags", "+faststart", out]
            subprocess.run(cmd, check=True)
            if os.path.getsize(out) <= 20 * 1024 * 1024 - 65536 or factor < 0.6:
                break
            factor -= 0.12

    final = os.path.getsize(out)
    if final > 20 * 1024 * 1024:
        raise SystemExit("Could not get the file under 20 MB (%.1f MB). Split the video into parts." % (final / 1048576))
    print(json.dumps({
        "path": os.path.abspath(out), "duration": round(src["duration"], 3), "w": w, "h": h,
        "fps": fps_i, "size_mb": round(final / 1048576, 2), "reencoded": reenc,
        "note": "low bitrate, picture will look soft" if reenc and long_edge == 640 else "",
    }))


if __name__ == "__main__":
    main()
