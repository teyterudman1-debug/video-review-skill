#!/usr/bin/env python3
"""Check a freshly rendered cut before the reviewer sees it.

    python3 qc_check.py <video> [--script script.txt] [--prev <previous version>] [--out qc]
                        [--at 1.2,4.8,...] [--every 1.5] [--target-lufs -14]
                        [--whisper-model <ggml .bin>] [--language en] [--no-transcript]

Claude cannot hear and does not watch the video play, so a re-edit can go wrong in ways a
person would spot in seconds. This runs the checks that can be automated and says plainly
which ones it could not run:

  transcript   speech-to-text of the final audio, compared word by word with --script
               (the words that should be spoken). Needs a local Whisper; skipped without one.
  loudness     integrated loudness and true peak (EBU R128), against the target and --prev.
  silence      gaps in the audio longer than they should be.
  black        black frames, which usually mean a missing clip or a bad cut point.
  frame sheet  a contact sheet of frames with their timecodes, to LOOK at: spelling and style
               of every caption and card, safe margins, the right shot under the right line.
               --at takes the times of each caption or changed moment; otherwise one frame
               every --every seconds.

Prints a Markdown report and writes into --out:
  qc.json      {"page": {...}, "ear": [...]}. Pass it to `page_ops.py add-cut --qc`; the "ear"
               list is a starting point for the check-by-ear list.
  words.json   every word heard, with the time it starts. Also useful on an existing cut, to
               find where a line is spoken before you edit.
  transcript.txt, sheet-01.png ...

The transcript comparison understands English number words; in other languages expect more
differences to read through.

Needs ffmpeg and ffprobe.
"""
import argparse
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from notes_report import write_png  # noqa: E402


def run(cmd, **k):
    return subprocess.run(cmd, capture_output=True, text=True, **k)


def probe(path):
    info = json.loads(run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], check=True).stdout)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        raise SystemExit("No video stream in " + path)
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    w, h = int(v["width"]), int(v["height"])
    rot = next((int(sd["rotation"]) for sd in v.get("side_data_list", []) or [] if "rotation" in sd), 0)
    if abs(rot) % 180 == 90:
        w, h = h, w
    return {"duration": float(info["format"].get("duration") or 0), "w": w, "h": h, "fps": fps or 30.0,
            "audio": any(s.get("codec_type") == "audio" for s in info["streams"])}


def mmss(t):
    t = max(0.0, float(t))
    return "%02d:%04.1f" % (int(t // 60), t % 60)


# ---------------------------------------------------------------- audio
def loudness(path):
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
    tail = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    get = lambda pat: (lambda m: float(m.group(1)) if m else None)(re.search(pat, tail))
    return {"I": get(r"I:\s+(-?[\d.]+) LUFS"), "LRA": get(r"LRA:\s+(-?[\d.]+) LU"), "TP": get(r"Peak:\s+(-?[\d.]+) dBFS")}


def silences(path, floor_db, min_len):
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "silencedetect=noise=%ddB:d=%.2f" % (floor_db, min_len), "-f", "null", "-"]).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [(float(a), float(b)) for a, b in re.findall(r"silence_end: (-?[\d.]+) \| silence_duration: ([\d.]+)", err)]
    return [(max(0.0, s), e, d) for s, (e, d) in zip(starts, ends)]


def blacks(path):
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an", "-vf", "blackdetect=d=0.12:pix_th=0.10", "-f", "null", "-"]).stderr
    return [(float(a), float(b), float(c)) for a, b, c in re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", err)]


# ---------------------------------------------------------------- transcript
def find_model(given):
    cands = [given, os.environ.get("WHISPER_MODEL")]
    for root in (os.getcwd(), os.path.expanduser("~")):
        for pat in ("tools/whisper-models/ggml-*.bin", "whisper-models/ggml-*.bin", "models/ggml-*.bin", ".cache/whisper.cpp/ggml-*.bin", ".cache/whisper/ggml-*.bin"):
            cands += sorted(glob.glob(os.path.join(root, pat)), key=os.path.getsize, reverse=True)
    return next((c for c in cands if c and os.path.isfile(c)), None)


def transcribe(video, out, model, lang):
    """Returns (words [(text, seconds)], how) or (None, reason it could not run)."""
    wav = os.path.join(out, "audio-16k.wav")
    if run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", wav]).returncode != 0:
        return None, "could not extract the audio"
    try:
        cli = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
        if cli:
            m = find_model(model)
            if not m:
                return None, "whisper-cli is installed but no model file was found. Pass --whisper-model <ggml-*.bin> or set WHISPER_MODEL."
            base = os.path.join(out, "transcript")
            r = run([cli, "-m", m, "-f", wav, "-l", lang, "-oj", "-ml", "1", "-sow", "-np", "-of", base])
            if r.returncode != 0 or not os.path.isfile(base + ".json"):
                return None, "whisper-cli failed: " + (r.stderr.strip().splitlines() or ["no output"])[-1][:160]
            segs = json.load(open(base + ".json", encoding="utf-8", errors="replace")).get("transcription", [])
            words = [(s["text"].strip(), s["offsets"]["from"] / 1000.0) for s in segs if s.get("text", "").strip()]
            return words, "whisper.cpp, " + os.path.basename(m)
        py = shutil.which("whisper") or shutil.which("mlx_whisper")
        if py:
            r = run([py, wav, "--language", lang, "--word_timestamps", "True", "--output_format", "json", "--output_dir", out])
            js = os.path.join(out, "audio-16k.json")
            if r.returncode != 0 or not os.path.isfile(js):
                return None, os.path.basename(py) + " failed: " + (r.stderr.strip().splitlines() or ["no output"])[-1][:160]
            words = []
            for seg in json.load(open(js, encoding="utf-8")).get("segments", []):
                ws = seg.get("words")
                if ws:
                    words += [(w["word"].strip(), float(w["start"])) for w in ws if w.get("word", "").strip()]
                else:
                    words += [(w, float(seg.get("start", 0))) for w in seg.get("text", "").split()]
            os.replace(js, os.path.join(out, "transcript.json"))
            return words, os.path.basename(py)
        return None, "no speech-to-text tool found (looked for whisper-cli, whisper, mlx_whisper)"
    finally:
        if os.path.exists(wav):
            os.remove(wav)


def norm_words(pairs):
    """Lowercase, strip punctuation, and join runs of single letters ("B B C" equals "BBC"),
    so the comparison is about the words that were said, not how they were typed."""
    out = []
    for text, t in pairs:
        for raw in re.split(r"[\s\u2014\u2013/-]+", text):
            w = re.sub(r"[^a-z0-9']+", "", raw.lower().replace("\u2019", "'")).strip("'")
            if w:
                out.append((w, t))
    joined, i = [], 0
    while i < len(out):
        if len(out[i][0]) == 1 and out[i][0].isalpha():
            j = i
            while j < len(out) and len(out[j][0]) == 1 and out[j][0].isalpha():
                j += 1
            if j - i >= 2:
                joined.append(("".join(w for w, _ in out[i:j]), out[i][1]))
                i = j
                continue
        joined.append(out[i])
        i += 1
    return joined


UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}


def squash(words):
    """One string for a run of words, with English number words turned into digits and a "to"
    between two numbers dropped, so "thirty nine ninety" equals "3990" and "three to seven" equals
    "3-7". Speech-to-text writes numbers and compounds its own way; that is not a wrong word."""
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w in TENS:
            n = TENS[w]
            if i + 1 < len(words) and words[i + 1] in UNITS and 0 < UNITS[words[i + 1]] < 10:
                n += UNITS[words[i + 1]]
                i += 1
            out.append(str(n))
        elif w in UNITS:
            out.append(str(UNITS[w]))
        else:
            out.append(w)
        i += 1
    kept = [w for k, w in enumerate(out) if not (w == "to" and 0 < k < len(out) - 1 and out[k - 1].isdigit() and out[k + 1].isdigit())]
    return "".join(kept).replace("'", "")


def compare(script_text, heard):
    want = norm_words([(script_text, None)])
    got = norm_words(heard)
    sm = difflib.SequenceMatcher(a=[w for w, _ in want], b=[w for w, _ in got], autojunk=False)
    diffs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or squash([w for w, _ in want[i1:i2]]) == squash([w for w, _ in got[j1:j2]]):
            continue
        t = got[min(j1, len(got) - 1)][1] if got else 0.0
        ctx = " ".join(w for w, _ in want[max(0, i1 - 3):i1])
        diffs.append({"t": round(t, 2), "script": " ".join(w for w, _ in want[i1:i2]), "heard": " ".join(w for w, _ in got[j1:j2]), "after": ctx})
    return diffs, round(sm.ratio(), 3)


# ---------------------------------------------------------------- frame sheet
FONT = {  # 3x5 digits and a colon
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111", "4": "101101111001001",
    "5": "111100111001111", "6": "111100111101111", "7": "111001001001001", "8": "111101111101111", "9": "111101111001111",
    ":": "000010000010000", ".": "000000000000010",
}


def label_overlay(path, w, h, labels, scale=3):
    """Transparent PNG with a small timecode in the corner of each cell. labels: [(x, y, text)]."""
    buf = bytearray(w * h * 4)

    def rect(x0, y0, x1, y1, rgba):
        for y in range(max(0, y0), min(h, y1)):
            row = y * w * 4
            for x in range(max(0, x0), min(w, x1)):
                buf[row + x * 4:row + x * 4 + 4] = rgba

    for x, y, text in labels:
        tw = len(text) * 4 * scale + scale
        rect(x, y, x + tw + scale, y + 7 * scale, bytes((0, 0, 0, 215)))
        cx = x + scale
        for ch in text:
            bits = FONT.get(ch)
            if bits:
                for i, b in enumerate(bits):
                    if b == "1":
                        px, py = cx + (i % 3) * scale, y + scale + (i // 3) * scale
                        rect(px, py, px + scale, py + scale, bytes((255, 255, 255, 255)))
            cx += 4 * scale
    write_png(path, w, h, buf)


def frame_sheets(video, info, times, out, cols=6, rows=4, cell_w=240):
    cell_h = int(round(cell_w * info["h"] / info["w"] / 2)) * 2
    tmp = os.path.join(out, "_frames")
    os.makedirs(tmp, exist_ok=True)
    kept = []
    for i, t in enumerate(times):
        dest = os.path.join(tmp, "f_%04d.png" % len(kept))
        r = run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0, t), "-i", video, "-frames:v", "1", "-vf", "scale=%d:%d" % (cell_w, cell_h), dest])
        if r.returncode == 0 and os.path.isfile(dest):
            kept.append(t)
    sheets, per = [], cols * rows
    for s in range(0, len(kept), per):
        chunk = kept[s:s + per]
        n_rows = (len(chunk) + cols - 1) // cols
        raw = os.path.join(tmp, "sheet_%02d.png" % (len(sheets) + 1))
        run(["ffmpeg", "-v", "error", "-y", "-start_number", str(s), "-framerate", "1", "-i", os.path.join(tmp, "f_%04d.png"), "-frames:v", "1",
             "-vf", "tile=%dx%d:padding=4:margin=4:color=0x202020" % (cols, n_rows), raw])
        if not os.path.isfile(raw):
            continue
        W, H = cols * cell_w + (cols + 1) * 4, n_rows * cell_h + (n_rows + 1) * 4
        ov = os.path.join(tmp, "labels_%02d.png" % (len(sheets) + 1))
        labels = [(4 + (i % cols) * (cell_w + 4) + 3, 4 + (i // cols) * (cell_h + 4) + 3, "%d:%05.2f" % (int(t // 60), t % 60)) for i, t in enumerate(chunk)]
        label_overlay(ov, W, H, labels)
        dest = os.path.join(out, "sheet-%02d.png" % (len(sheets) + 1))
        r = run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-i", ov, "-filter_complex", "overlay", "-frames:v", "1", dest])
        if r.returncode != 0:
            shutil.copy(raw, dest)
        sheets.append((dest, chunk[0], chunk[-1], len(chunk)))
    shutil.rmtree(tmp, ignore_errors=True)
    return sheets


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--script", help="text file of the words that should be spoken")
    ap.add_argument("--prev", help="the previous version, for loudness and length comparison")
    ap.add_argument("--out", default="qc")
    ap.add_argument("--at", help="comma-separated times in seconds to put on the frame sheet (each caption, each changed moment)")
    ap.add_argument("--every", type=float, help="seconds between frames on the sheet when --at is not given")
    ap.add_argument("--target-lufs", type=float, default=-14.0)
    ap.add_argument("--whisper-model")
    ap.add_argument("--language", default="en")
    ap.add_argument("--no-transcript", action="store_true")
    a = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(tool + " is not installed, so no checks can run.")
    if not os.path.isfile(a.video):
        raise SystemExit("No such file: " + a.video)
    os.makedirs(a.out, exist_ok=True)
    info = probe(a.video)
    prev = probe(a.prev) if a.prev and os.path.isfile(a.prev) else None
    items, ear, lines = [], [], []

    def item(name, state, detail):
        items.append({"name": name, "state": state, "detail": detail})
        lines.append("- **%s**: %s. %s" % (name, {"ok": "passed", "look": "LOOK", "skip": "NOT RUN"}[state], detail))

    lines.append("# Checks on %s" % os.path.basename(a.video))
    d = "%.1fs, %dx%d, %.5g fps" % (info["duration"], info["w"], info["h"], info["fps"])
    if prev:
        delta = info["duration"] - prev["duration"]
        d += "; the previous version was %.1fs (%+.1fs)" % (prev["duration"], delta)
    lines.append(d + "\n")

    # transcript
    heard = None
    if not info["audio"]:
        item("Transcript against script", "skip", "The video has no audio track.")
    elif a.no_transcript:
        item("Transcript against script", "skip", "Turned off with --no-transcript.")
    else:
        heard, how = transcribe(a.video, a.out, a.whisper_model, a.language)
        if heard is None:
            item("Transcript against script", "skip", "Could not transcribe: %s. Every spoken line needs checking by ear." % how)
            ear.append({"text": "No transcript could be made, so listen to the whole voice track against the script.", "t": None})
        else:
            with open(os.path.join(a.out, "transcript.txt"), "w", encoding="utf-8") as f:
                f.write(" ".join(w for w, _ in heard) + "\n")
            json.dump([{"w": w, "t": t} for w, t in heard], open(os.path.join(a.out, "words.json"), "w"))
            if a.script and os.path.isfile(a.script):
                diffs, ratio = compare(open(a.script, encoding="utf-8").read(), heard)
                if not diffs:
                    item("Transcript against script", "ok", "Every word of the script was heard, in order (%s)." % how)
                else:
                    shown = "; ".join("%s script \"%s\" heard \"%s\"" % (mmss(x["t"]), x["script"] or "(nothing)", x["heard"] or "(nothing)") for x in diffs[:8])
                    item("Transcript against script", "look", "%d %s differ (%s): %s%s. Speech-to-text mishears names, numbers and short words, so each one needs an ear before it is called a fault." % (
                        len(diffs), "place" if len(diffs) == 1 else "places", how, shown, " ..." if len(diffs) > 8 else ""))
                    for x in diffs[:12]:
                        ear.append({"text": "Listen: the script says \"%s\", the transcript heard \"%s\"." % (x["script"] or "(nothing)", x["heard"] or "(nothing)"), "t": max(0.0, round(x["t"] - 0.5, 1))})
                json.dump(diffs, open(os.path.join(a.out, "transcript-diff.json"), "w"), indent=1)
            else:
                item("Transcript against script", "skip", "Transcribed (%s) and saved to transcript.txt, but no --script was given to compare it with. Read it against the intended words." % how)

    # loudness
    if info["audio"]:
        L = loudness(a.video)
        if L["I"] is None:
            item("Loudness", "skip", "ffmpeg could not measure it.")
        else:
            notes, state = [], "ok"
            if abs(L["I"] - a.target_lufs) > 2.0:
                state = "look"
                notes.append("%.1f LU from the %.0f LUFS target" % (L["I"] - a.target_lufs, a.target_lufs))
            if L["TP"] is not None and L["TP"] > -1.0:
                state = "look"
                notes.append("true peak %.1f dBFS is above -1, which can clip after upload" % L["TP"])
            if prev and prev["audio"]:
                P = loudness(a.prev)
                if P["I"] is not None:
                    notes.append("previous version %.1f LUFS" % P["I"])
                    if abs(L["I"] - P["I"]) > 1.5:
                        state = "look"
                        ear.append({"text": "Overall level moved %.1f LU from the last version. Check it does not sound louder or quieter than you want." % (L["I"] - P["I"]), "t": None})
            item("Loudness", state, "%.1f LUFS integrated, range %.1f LU, true peak %s dBFS%s" % (
                L["I"], L["LRA"] or 0, "%.1f" % L["TP"] if L["TP"] is not None else "?", "; " + "; ".join(notes) if notes else ""))
        sil = [s for s in silences(a.video, -45, 0.9) if s[0] > 0.3 and s[1] < info["duration"] - 0.3]
        if not sil:
            item("Silent gaps", "ok", "No gap in the audio longer than 0.9s inside the video.")
        else:
            long = [s for s in sil if s[2] >= 1.5]
            item("Silent gaps", "look" if long else "ok", "%d %s: %s. A music bed should cover these; if a gap is meant, say so." % (
                len(sil), "gap" if len(sil) == 1 else "gaps", ", ".join("%s for %.1fs" % (mmss(s[0]), s[2]) for s in sil[:8])))
            for s in long[:4]:
                ear.append({"text": "The audio drops out for %.1fs here. Check it is meant to." % s[2], "t": round(s[0], 1)})
    else:
        item("Loudness", "skip", "The video has no audio track.")

    # black frames
    bl = blacks(a.video)
    mid = [b for b in bl if b[0] > 0.1 and b[1] < info["duration"] - 0.1]
    if not bl:
        item("Black frames", "ok", "None found.")
    else:
        item("Black frames", "look" if mid else "ok", "%s: %s." % ("Inside the video" if mid else "Only at the very start or end", ", ".join("%s for %.2fs" % (mmss(b[0]), b[2]) for b in (mid or bl)[:8])))

    # frame sheet
    if a.at:
        times = sorted({round(float(x), 2) for x in a.at.split(",") if x.strip()})
        times = [t for t in times if 0 <= t < info["duration"]]
    else:
        every = a.every or min(3.0, max(0.5, info["duration"] / 48.0))
        times = [round(i * every + min(0.2, every / 2), 2) for i in range(int(info["duration"] / every) + 1) if i * every + 0.2 < info["duration"]]
    sheets = frame_sheets(a.video, info, times, a.out) if times else []
    if sheets:
        item("Caption frame sheet", "look", "%d frames on %d %s. This check is done by eye: open each sheet and read every caption and card for spelling, font, position and the right shot." % (
            len(times), len(sheets), "sheet" if len(sheets) == 1 else "sheets"))
    else:
        item("Caption frame sheet", "skip", "No frames could be pulled.")

    print("\n".join(lines))
    if sheets:
        print("\nFrame sheets to look at (timecodes are in each frame's corner):")
        for p, t0, t1, n in sheets:
            print("- %s  (%s to %s, %d frames)" % (p, mmss(t0), mmss(t1), n))
    if heard is not None:
        print("\nTranscript: %s" % os.path.join(os.path.abspath(a.out), "transcript.txt"))
    if ear:
        print("\nSuggested check-by-ear items (add your own: every new or changed spoken line, any name or number, music level under the voice):")
        for e in ear:
            print("- %s%s" % (e["text"], "  [%s]" % mmss(e["t"]) if e.get("t") is not None else ""))
    res = {"page": {"ranAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items}, "ear": ear,
           "video": {"duration": round(info["duration"], 3), "w": info["w"], "h": info["h"], "fps": info["fps"]},
           "sheets": [s[0] for s in sheets]}
    with open(os.path.join(a.out, "qc.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print("\nqc.json: %s" % os.path.join(os.path.abspath(a.out), "qc.json"))


if __name__ == "__main__":
    main()
