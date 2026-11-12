#!/usr/bin/env python3
"""Turn what is saved on a review page into an edit list Claude can act on.

    python3 notes_report.py --db <dir> --cut <video id> [--state .video-review.json] [--out <dir>] [--all]

--db     the directory ArtifactData wrote with `out_dir`: it holds `comments/` and `videos/`
         (and `rounds/`, `checks/` if they were saved). One JSON file per document.
--cut    the id of any cut of the video. The report covers every note on that video (all its
         cuts and versions), because a note left on one version or one sibling cut still
         applies to the next version unless it has been dealt with.
--state  the project's .video-review.json. It maps each cut to its full-quality source file,
         which is where the frames are pulled from. Without it, pass --video for one cut.
--video  a video file for the cut named by --cut, when there is no state file.
--out    where to write the frames and notes.json (default: review-notes).
--all    also list resolved notes in full.

Prints a Markdown edit list. Open notes come first, numbered as the page numbers them
(#1, #2 for notes at a frame; W1, W2 for whole-video notes), each with its exact time, who it
applies to, replies, and a PNG of the frame with the reviewer's drawing over it in magenta.
Also writes <out>/notes.json, which page_ops.py uses to check replies against.

Needs ffmpeg and ffprobe only for the frames.
"""
import argparse
import glob
import json
import os
import re
import struct
import subprocess
import sys
import zlib


# ---------------------------------------------------------------- loading
def load_dir(path):
    """{id: body} for every JSON document under a directory (or a single list file)."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    if os.path.isfile(path):
        d = json.load(open(path, encoding="utf-8"))
        docs = d if isinstance(d, list) else d.get("documents") or d.get("docs") or [d]
        for x in docs:
            if isinstance(x, dict):
                body = x.get("data") if isinstance(x.get("data"), dict) else x
                out[str(x.get("id") or body.get("id") or len(out))] = body
        return out
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(d, dict):
            continue
        if "id" in d and isinstance(d.get("data"), dict):      # wrapped as {"id", "data"}
            out[str(d["id"])] = d["data"]
        else:                                                    # bare document, named by its file
            out[os.path.splitext(os.path.basename(f))[0]] = d
    return out


def load_db(path):
    """Accepts the out_dir itself, or its comments/ folder (the old layout)."""
    if os.path.isdir(os.path.join(path, "comments")):
        root = path
    elif os.path.basename(os.path.normpath(path)) == "comments":
        root = os.path.dirname(os.path.normpath(path))
    else:
        root = None
    if root is None:
        return {"comments": load_dir(path), "videos": {}, "rounds": {}, "checks": {}}
    return {k: load_dir(os.path.join(root, k)) for k in ("comments", "videos", "rounds", "checks")}


# ---------------------------------------------------------------- versions and states (same rules as the page)
def vinfo(v):
    variant, version = v.get("variant"), v.get("version")
    if variant is None or version is None:
        m = re.match(r"^(.*?)[\s,_-]*\bv(\d+)\s*$", v.get("label") or "", re.I)
        if variant is None:
            variant = (m.group(1).strip() or "Main") if m else (v.get("label") or "Main")
        if version is None:
            version = int(m.group(2)) if m else 1
    return str(variant), int(version or 1)


def is_whole(n):
    return n.get("general") is True or n.get("t") is None


def note_key(n):
    return (0 if is_whole(n) else 1, float(n.get("t") or 0), str(n.get("createdAt")))


def tc(t, fps):
    fps = int(fps or 30)
    n = int(float(t) * fps + 1e-4)
    s, fr = divmod(n, fps)
    return "%02d:%02d:%02d" % (s // 60, s % 60, fr)


class Page:
    def __init__(self, db):
        self.videos = db["videos"]
        self.comments = db["comments"]
        self.rounds = db.get("rounds", {})
        self.kids = {}
        self.tops = {}
        for cid, c in self.comments.items():
            if c.get("parentId"):
                self.kids.setdefault(c["parentId"], []).append(dict(c, id=cid))
            else:
                self.tops[cid] = dict(c, id=cid)
        for v in self.kids.values():
            v.sort(key=lambda r: str(r.get("createdAt")))

    def group_of(self, vid):
        return (self.videos.get(vid) or {}).get("group") or "Ungrouped"

    def cuts_in(self, group):
        return {k: v for k, v in self.videos.items() if (v.get("group") or "Ungrouped") == group}

    def numbers(self, vid):
        out, w, n = {}, 0, 0
        for c in sorted((c for c in self.tops.values() if c.get("videoId") == vid), key=note_key):
            if is_whole(c):
                w += 1
                out[c["id"]] = "W%d" % w
            else:
                n += 1
                out[c["id"]] = "#%d" % n
        return out

    def fix(self, c):
        m = [r for r in self.kids.get(c["id"], []) if r.get("author") == "claude" and r.get("kind") in ("done", "partly") and r.get("addressedIn")]
        return m[-1] if m else None

    def triage(self, c):
        m = [r for r in self.kids.get(c["id"], []) if r.get("author") == "claude" and r.get("kind") and not r.get("addressedIn")]
        return m[-1] if m else None

    def state(self, c):
        return "resolved" if c.get("resolved") else "addressed" if self.fix(c) else "open"


# ---------------------------------------------------------------- frames
def where(strokes):
    xs = [p for s in strokes for p in s[0::2]]
    ys = [p for s in strokes for p in s[1::2]]
    if not xs:
        return ""
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    col = "left" if cx < 0.36 else "right" if cx > 0.64 else "centre"
    row = "top" if cy < 0.36 else "bottom" if cy > 0.64 else "middle"
    spot = "centre" if (row, col) == ("middle", "centre") else (row + " " + col).replace("middle ", "")
    return "drawing in the %s of the frame (x %.0f-%.0f%%, y %.0f-%.0f%%)" % (
        spot, min(xs) * 100, max(xs) * 100, min(ys) * 100, max(ys) * 100)


def write_png(path, w, h, buf):
    raw = b"".join(b"\x00" + bytes(buf[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def write_overlay(path, w, h, strokes):
    """A transparent PNG of the strokes, written with the standard library only."""
    buf = bytearray(w * h * 4)

    def disc(cx, cy, r, rgba):
        x0, x1 = max(0, int(cx - r)), min(w - 1, int(cx + r))
        y0, y1 = max(0, int(cy - r)), min(h - 1, int(cy + r))
        r2 = r * r
        for y in range(y0, y1 + 1):
            dy2 = (y - cy) ** 2
            row = y * w * 4
            for x in range(x0, x1 + 1):
                if (x - cx) ** 2 + dy2 <= r2:
                    buf[row + x * 4:row + x * 4 + 4] = rgba

    def line(pts, r, rgba):
        px = [(pts[i] * w, pts[i + 1] * h) for i in range(0, len(pts) - 1, 2)]
        if len(px) == 1:
            px.append(px[0])
        for (ax, ay), (bx, by) in zip(px, px[1:]):
            steps = max(1, int(max(abs(bx - ax), abs(by - ay)) / max(1.0, r / 2)))
            for i in range(steps + 1):
                t = i / steps
                disc(ax + (bx - ax) * t, ay + (by - ay) * t, r, rgba)

    r = max(2.5, w * 0.0055)
    for s in strokes:
        line(s, r + 2, bytes((0, 0, 0, 150)))
    for s in strokes:
        line(s, r, bytes((255, 60, 200, 255)))  # magenta: rarely clashes with footage
    write_png(path, w, h, buf)


def frame_size(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:stream_side_data=rotation", "-of", "json", video],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    rot = next((int(sd["rotation"]) for sd in s.get("side_data_list", []) or [] if "rotation" in sd), 0)
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


def grab(video, t, strokes, dest, size):
    w, h = size
    k = min(1.0, 960 / max(w, h))
    ow, oh = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    base = ["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0, t), "-i", video]
    if strokes:
        ov = dest + ".overlay.png"
        write_overlay(ov, ow, oh, strokes)
        cmd = base + ["-i", ov, "-filter_complex", "[0:v]scale=%d:%d[b];[b][1:v]overlay" % (ow, oh), "-frames:v", "1", dest]
    else:
        cmd = base + ["-vf", "scale=%d:%d" % (ow, oh), "-frames:v", "1", dest]
    ok = subprocess.run(cmd).returncode == 0 and os.path.exists(dest)
    if strokes and os.path.exists(dest + ".overlay.png"):
        os.remove(dest + ".overlay.png")
    return ok


# ---------------------------------------------------------------- report
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", "--notes", dest="db", required=True)
    ap.add_argument("--cut", required=True)
    ap.add_argument("--state")
    ap.add_argument("--video")
    ap.add_argument("--out", default="review-notes")
    ap.add_argument("--fps", type=int, help="only needed when the videos collection was not saved")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    page = Page(load_db(a.db))
    if not page.comments and not page.videos:
        raise SystemExit("Nothing found under %s. Save the page's `comments` and `videos` collections there first (ArtifactData list with out_dir)." % a.db)
    if a.cut not in page.videos:
        if page.videos:
            raise SystemExit("No cut with id %r. Cuts on the page: %s" % (a.cut, ", ".join(sorted(page.videos))))
        page.videos[a.cut] = {"group": "Ungrouped", "label": a.cut, "fps": a.fps or 30}   # old layout: notes only

    group = page.group_of(a.cut)
    cuts = page.cuts_in(group)
    variants = {}
    for vid, v in cuts.items():
        variants.setdefault(vinfo(v)[0], []).append((vinfo(v)[1], str(v.get("createdAt")), vid))
    for k in variants:
        variants[k].sort()
    latest = {k: v[-1][2] for k, v in variants.items()}
    label = lambda vid: (cuts.get(vid) or {}).get("label") or vid
    fps_of = lambda vid: int((cuts.get(vid) or {}).get("fps") or a.fps or 30)

    # sources for frames
    sources = {}
    if a.state and os.path.isfile(a.state):
        st = json.load(open(a.state, encoding="utf-8"))
        base = os.path.dirname(os.path.abspath(a.state))
        for vid, c in (st.get("cuts") or {}).items():
            src = c.get("source")
            if src:
                p = src if os.path.isabs(src) else os.path.join(base, src)
                if os.path.isfile(p):
                    sources[vid] = p
    if a.video:
        if not os.path.isfile(a.video):
            raise SystemExit("No such video: " + a.video)
        sources[a.cut] = a.video
    sizes = {}

    notes = sorted((c for c in page.tops.values() if c.get("videoId") in cuts),
                   key=lambda c: (vinfo(cuts[c["videoId"]])[1], label(c["videoId"]), note_key(c)))
    numbers = {}
    for vid in cuts:
        numbers.update(page.numbers(vid))

    # two notes with the same words at the same frame, seconds apart, are almost always one note posted twice
    dupes = {}
    for i, x in enumerate(notes):
        for y in notes[:i]:
            same_t = (is_whole(x) and is_whole(y)) or (not is_whole(x) and not is_whole(y) and abs(float(x.get("t") or 0) - float(y.get("t") or 0)) < 0.05)
            if x.get("videoId") == y.get("videoId") and same_t and (x.get("text") or "").strip().lower() == (y.get("text") or "").strip().lower() and (x.get("text") or "").strip():
                dupes[x["id"]] = y["id"]

    def applies(c):
        home = vinfo(cuts[c["videoId"]])[0]
        return [home] if c.get("variantOnly") or len(variants) < 2 else sorted(variants)

    os.makedirs(a.out, exist_ok=True)
    rows, by_state = [], {"open": [], "addressed": [], "resolved": []}
    for c in notes:
        by_state[page.state(c)].append(c)

    print("# Notes on %s" % group)
    for name in sorted(variants):
        vs = variants[name]
        print("- %s: %s (latest: `%s`)" % (name, ", ".join("v%d" % x[0] for x in vs), vs[-1][2]))
    print("\n%d open, %d addressed and waiting for the reviewer's tick, %d resolved.\n" % (
        len(by_state["open"]), len(by_state["addressed"]), len(by_state["resolved"])))
    live = sorted((r for r in page.rounds.values() if r.get("group") == group and r.get("state") not in ("done", "withdrawn")), key=lambda r: str(r.get("submittedAt")))
    if live:
        r = live[-1]
        print("Round: %s on %s, %d notes, state `%s`.\n" % ("submitted", str(r.get("submittedAt"))[:16].replace("T", " "), int(r.get("count") or 0), r.get("state")))

    def emit(c, full=True):
        vid, fps = c["videoId"], fps_of(c["videoId"])
        num = numbers.get(c["id"], "?")
        if is_whole(c):
            span, secs = "whole video", None
        else:
            t = float(c.get("t") or 0)
            span = tc(t, fps) + (" to " + tc(c["tEnd"], fps) if c.get("tEnd") is not None else "")
            secs = "%.2fs" % t + (" to %.2fs" % float(c["tEnd"]) if c.get("tEnd") is not None else "")
        strokes = [s for s in (c.get("strokes") or []) if isinstance(s, list) and len(s) >= 2]
        row = {"id": c["id"], "number": num, "cut": vid, "label": label(vid), "whole": is_whole(c), "t": c.get("t"), "tEnd": c.get("tEnd"),
               "state": page.state(c), "appliesTo": applies(c), "text": c.get("text") or "", "via": c.get("via"), "frame": None,
               "duplicateOf": dupes.get(c["id"])}
        print("### %s on %s  ·  %s%s" % (num, label(vid), span, "  (%s)" % secs if secs else ""))
        print("- note id: `%s`" % c["id"])
        print("- says: %s" % (c.get("text") or "(no text, drawing only)").replace("\n", "\n  "))
        tags = []
        if c.get("via") == "chat":
            tags.append("said in chat, recorded here")
        if c.get("editedAt"):
            tags.append("edited by the reviewer")
        if tags:
            print("- " + "; ".join(tags))
        print("- applies to: %s" % ", ".join(applies(c)) + (" only" if c.get("variantOnly") and len(variants) > 1 else ""))
        if c["id"] in dupes:
            print("- LOOKS LIKE A DOUBLE POST of %s (same words, same frame). Treat the two as one note; reply on both." % numbers.get(dupes[c["id"]], dupes[c["id"]]))
        if strokes:
            print("- " + where(strokes))
        if full and not is_whole(c):
            src = sources.get(vid)
            if src:
                if vid not in sizes:
                    try:
                        sizes[vid] = frame_size(src)
                    except Exception:
                        sizes[vid] = None
                if sizes[vid]:
                    dest = os.path.join(a.out, "%s_%s_%s.png" % (re.sub(r"[^a-z0-9]+", "-", vid.lower()), num.replace("#", "note-"), tc(float(c.get("t") or 0), fps).replace(":", "-")))
                    if grab(src, float(c.get("t") or 0), strokes, dest, sizes[vid]):
                        row["frame"] = os.path.abspath(dest)
                        print("- frame%s: %s" % (" with the drawing in magenta" if strokes else "", os.path.abspath(dest)))
            elif strokes:
                print("- no frame image: the source file for `%s` is not in the state file. Pass --video or record its source." % vid)
        tri, fix = page.triage(c), page.fix(c)
        for r in page.kids.get(c["id"], []):
            who = "Claude" if r.get("author") == "claude" else "reviewer"
            kind = " [%s%s]" % (r["kind"], " in " + label(r["addressedIn"]) if r.get("addressedIn") else "") if r.get("kind") else ""
            at = " at %s" % tc(r["addressedAt"], fps_of(r["addressedIn"])) if r.get("addressedAt") is not None and r.get("addressedIn") in cuts else ""
            print("- reply (%s%s%s): %s" % (who, kind, at, (r.get("text") or "").replace("\n", " ")))
        if fix:
            row["addressedIn"], row["addressedAt"] = fix.get("addressedIn"), fix.get("addressedAt")
        if tri:
            row["triage"] = tri.get("kind")
        after = [r for r in page.kids.get(c["id"], []) if r.get("author") != "claude" and fix and str(r.get("createdAt")) > str(fix.get("createdAt"))]
        if after:
            print("- THE REVIEWER REPLIED AFTER THE FIX. Read that reply: the note may not be settled.")
            row["replyAfterFix"] = True
        print()
        rows.append(row)

    print("## Open: act on these (%d)\n" % len(by_state["open"]))
    if not by_state["open"]:
        print("Nothing open.\n")
    for c in by_state["open"]:
        emit(c)
    if by_state["addressed"]:
        print("## Addressed, waiting for the reviewer's tick (%d)\n" % len(by_state["addressed"]))
        print("Already dealt with in an earlier version. Do not redo these unless the reviewer replied after the fix.\n")
        for c in by_state["addressed"]:
            emit(c, full=False)
    if by_state["resolved"]:
        print("## Resolved (%d)\n" % len(by_state["resolved"]))
        if a.all:
            for c in by_state["resolved"]:
                emit(c, full=False)
        else:
            for c in by_state["resolved"]:
                rows.append({"id": c["id"], "number": numbers.get(c["id"]), "cut": c["videoId"], "label": label(c["videoId"]), "state": "resolved", "text": c.get("text") or ""})
            print("Not listed. Pass --all to see them.\n")

    with open(os.path.join(a.out, "notes.json"), "w", encoding="utf-8") as f:
        json.dump({"group": group, "latest": latest, "cuts": {k: {"label": label(k), "variant": vinfo(v)[0], "version": vinfo(v)[1]} for k, v in cuts.items()}, "notes": rows}, f, indent=1)
    print("notes.json: %s" % os.path.abspath(os.path.join(a.out, "notes.json")))


if __name__ == "__main__":
    main()
