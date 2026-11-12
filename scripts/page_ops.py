#!/usr/bin/env python3
"""Prepare the documents Claude writes to a review page, so every field is right first time.

Claude writes to the page with the ArtifactData tool. This script does not call that tool; it
builds the documents, checks them against a saved copy of the page, writes each one to a file
and prints the `writes` list to pass to ArtifactData `action: "batch"`. Every write here creates
a new document, so no `if_version` is needed and nothing the reviewer wrote is ever changed.

    python3 page_ops.py --db <dir> --out <dir> <command> ...

--db   the directory ArtifactData wrote with `out_dir` (holds comments/ and videos/). Optional
       for add-cut on an empty page.
--out  where to write the document files (a scratch directory).

Commands

  add-cut   a new cut or a new version of one
      --group "Launch film" --label "Main v2" --asset-id <id> --copy <review_copy.py JSON or file>
      [--summary "what changed, one or two sentences"] [--qc <qc.json from qc_check.py>] [--id slug]

  replies   Claude's replies under notes: what will be done, what cannot, what was done
      --file replies.json
      [ { "note": "<note id>", "kind": "done", "text": "...", "in": "<new cut id>", "at": 12.4 }, ... ]
      kind is one of:
        can     will do, nothing to ask                       (before the edit; optional)
        plan    this is what I intend, waiting for a go       (before the edit)
        partly  only part is possible, with the reason        (before or after; add "in" once it is in a version)
        cannot  not possible, with the reason                 (before or after)
        done    changed, in the version named by "in"         (after the edit)
      "in" is the id of the cut the change is in. "at" is the time in seconds, in that cut, where
      the reviewer should look (leave it out for a change that runs through the whole video).

  chat-note  feedback the reviewer gave in chat, recorded on the version it was about
      --cut <video id> --text "their exact words" [--author <user id>]

  checks    the check-by-ear list for a version
      --cut <video id> --file ear.json     [ { "text": "...", "t": 12.4 }, ... ]

  round     the data for moving the reviewer's submitted round on (an update, so it needs the
            round's current version from an ArtifactData read)
      --group "Launch film" --state plan|working|done [--note "one line shown beside the plan"]

  file      where a cut's full-quality file is, for a cut that is already on the page, so the
            page's "Show in Finder" button appears for it (an update, like round)
      --cut <video id> --source <path to the full-quality file>
"""
import argparse
import glob
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from notes_report import Page, load_db, vinfo  # noqa: E402

KINDS = ("can", "plan", "partly", "cannot", "done")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(s):
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", str(s).lower()))[:60] or "cut"


def die(msg):
    print("page_ops: " + msg, file=sys.stderr)
    sys.exit(2)


class Out:
    def __init__(self, out_dir):
        self.dir = os.path.abspath(out_dir)
        self.writes = []

    def create(self, collection, doc_id, body):
        d = os.path.join(self.dir, collection)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, doc_id + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=1, ensure_ascii=False)
        self.writes.append({"op": "set", "collection": collection, "doc_id": doc_id, "file_path": path})

    def finish(self, extra=None):
        res = {"writes": self.writes}
        res.update(extra or {})
        res["next"] = ("Pass `writes` to ArtifactData with action \"batch\" and the page's url. "
                       "At most 50 writes per batch; split the list if it is longer.")
        print(json.dumps(res, indent=1, ensure_ascii=False))


def read_json_arg(value):
    """A JSON string, or a path to a JSON file."""
    if value is None:
        return None
    if os.path.isfile(value):
        return json.load(open(value, encoding="utf-8"))
    try:
        return json.loads(value)
    except ValueError:
        die("could not read %r as JSON or as a file" % value[:80])


def full_path(source):
    path = os.path.abspath(os.path.expanduser(source))
    if not os.path.isfile(path):
        die("--source %s is not a file on this computer" % path)
    return path


def cmd_file(a, page, out):
    if a.cut not in page.videos:
        die("no cut with id %r on the page" % a.cut)
    print(json.dumps({"cut": a.cut, "update": {"collection": "videos", "doc_id": a.cut, "data": {"file": full_path(a.source)}},
                      "next": "ArtifactData action \"update\" with this collection, doc_id and data, and if_version set to the row's current version (read it with action \"get\" first)."}, indent=1))


def cmd_add_cut(a, page, out):
    info = read_json_arg(a.copy) or {}
    group, label = " ".join(a.group.split()), " ".join(a.label.split())
    variant, version = vinfo({"label": label})
    same = {k: v for k, v in page.videos.items() if (v.get("group") or "Ungrouped") == group}
    for k, v in same.items():
        if vinfo(v) == (variant, version):
            die("%s already has %s (id %s). Give the new version the next number, for example \"%s v%d\"." % (group, label, k, variant, max(vinfo(x)[1] for x in same.values() if vinfo(x)[0] == variant) + 1))
    older = sorted((vinfo(v)[1], k) for k, v in same.items() if vinfo(v)[0] == variant and vinfo(v)[1] < version)
    doc_id = a.id
    if not doc_id and older:      # keep the naming of the version before it: launch-main-v1 -> launch-main-v2
        guess = re.sub(r"v\d+$", "v%d" % version, older[-1][1])
        if guess != older[-1][1] and guess not in page.videos:
            doc_id = guess
    doc_id = doc_id or slug(group + "-" + label)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", doc_id):
        die("--id must be lowercase letters, digits and hyphens")
    if doc_id in page.videos:
        die("a cut with id %s is already on the page" % doc_id)
    for need in ("duration", "w", "h", "fps"):
        if need not in info:
            die("--copy must carry %s (pass the JSON review_copy.py printed)" % need)
    body = {
        "group": group, "label": label, "variant": variant, "version": version,
        "prev": older[-1][1] if older else None, "status": "review",
        "assetId": a.asset_id, "duration": info["duration"], "w": info["w"], "h": info["h"], "fps": info["fps"],
        "createdAt": now(), "createdBy": None,
    }
    if a.source:
        body["file"] = full_path(a.source)
    if a.summary:
        body["summary"] = a.summary.strip()
    qc = read_json_arg(a.qc)
    if qc:
        body["qc"] = qc.get("page") or qc
    out.create("videos", doc_id, body)
    sibs = sorted({vinfo(v)[0] for v in same.values()} - {variant})
    out.finish({
        "cut": doc_id,
        "state_file_entry": {doc_id: {"source": a.source or "<path to the full-quality file>", "group": group, "label": label}},
        "siblings": sibs,
        "previous_version": body["prev"],
    })


def cmd_replies(a, page, out):
    items = read_json_arg(a.file)
    if not isinstance(items, list) or not items:
        die("--file must hold a list of replies")
    known_cuts = set(page.videos) | set(a.new_cut or [])
    stamp = now()
    for i, it in enumerate(items):
        nid, kind, text = it.get("note"), it.get("kind"), (it.get("text") or "").strip()
        if nid not in page.tops:
            die("reply %d: no note with id %r on the page. Use the `note id` from notes_report.py." % (i + 1, nid))
        if kind not in KINDS:
            die("reply %d: kind must be one of %s" % (i + 1, ", ".join(KINDS)))
        if not text:
            die("reply %d: text is empty. Say what changed, or why not." % (i + 1))
        note = page.tops[nid]
        body = {"videoId": note["videoId"], "parentId": nid, "t": note.get("t"), "tEnd": None, "text": text,
                "author": "claude", "kind": kind, "createdAt": stamp, "resolved": False, "strokes": None}
        where = it.get("in")
        if kind == "done" and not where:
            die("reply %d: a `done` reply must name the cut the change is in (\"in\": \"<cut id>\")" % (i + 1))
        if where:
            if kind not in ("done", "partly"):
                die("reply %d: only `done` and `partly` replies take \"in\"" % (i + 1))
            if where not in known_cuts:
                die("reply %d: cut %r is not on the page. If you are adding it in this same batch, pass --new-cut %s." % (i + 1, where, where))
            body["addressedIn"] = where
            at = it.get("at")
            if at is not None:
                dur = (page.videos.get(where) or {}).get("duration")
                if not isinstance(at, (int, float)) or at < 0 or (dur and at > dur):
                    die("reply %d: \"at\" must be a time in seconds inside %s" % (i + 1, where))
            body["addressedAt"] = at
        out.create("comments", "claude-%s-%s-%s" % (kind, slug(nid)[:10], secrets.token_hex(3)), body)
    out.finish({"replies": len(items)})


def cmd_chat_note(a, page, out):
    if a.cut not in page.videos:
        die("no cut with id %r on the page" % a.cut)
    text = a.text.strip()
    if not text:
        die("--text is empty")
    author = a.author
    if not author:
        seen = [c.get("author") for c in page.tops.values() if c.get("author") not in (None, "claude", "chat")]
        author = max(set(seen), key=seen.count) if seen else "chat"
    for c in page.tops.values():
        if c.get("videoId") == a.cut and c.get("via") == "chat" and (c.get("text") or "").strip() == text:
            die("that chat note is already on %s (note %s)" % (a.cut, c["id"]))
    out.create("comments", "chat-%s-%s" % (slug(a.cut)[:24], secrets.token_hex(3)), {
        "videoId": a.cut, "parentId": None, "t": None, "tEnd": None, "general": True, "variantOnly": bool(a.only),
        "text": text, "author": author, "via": "chat", "createdAt": a.when or now(), "resolved": False, "strokes": None})
    out.finish({"note": out.writes[-1]["doc_id"]})


def cmd_checks(a, page, out):
    if a.cut not in page.videos and a.cut not in (a.new_cut or []):
        die("no cut with id %r on the page. If you are adding it in this same batch, pass --new-cut %s." % (a.cut, a.cut))
    items = read_json_arg(a.file)
    if not isinstance(items, list) or not items:
        die("--file must hold a list like [{\"text\": \"...\", \"t\": 12.4}]")
    stamp = now()
    for i, it in enumerate(items):
        text = (it.get("text") if isinstance(it, dict) else str(it) or "").strip()
        if not text:
            die("check %d has no text" % (i + 1))
        t = it.get("t") if isinstance(it, dict) else None
        out.create("checks", "ear-%s-%02d-%s" % (slug(a.cut)[:24], i + 1, secrets.token_hex(2)),
                   {"videoId": a.cut, "kind": "ear", "text": text, "t": t, "order": i + 1, "done": False, "createdAt": stamp})
    out.finish({"checks": len(items)})


def cmd_round(a, page, out):
    live = sorted(((str(r.get("submittedAt")), rid, r) for rid, r in page.rounds.items()
                   if r.get("group") == a.group and r.get("state") not in ("done", "withdrawn")))
    data = {"state": a.state, a.state + "At": now()}
    if a.note:
        data["planNote"] = a.note.strip()
    if not live:
        print(json.dumps({"round": None, "next": "No submitted round for this video on the saved copy of the page (the reviewer asked in chat, or `rounds` was not saved). Nothing to update; carry on."}, indent=1))
        return
    _, rid, r = live[-1]
    print(json.dumps({"round": rid, "was": r.get("state"), "update": {"collection": "rounds", "doc_id": rid, "data": data},
                      "next": "ArtifactData action \"update\" with this collection, doc_id and data, and if_version set to the round's current version (read it with action \"get\" first)."}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db")
    ap.add_argument("--out", default="page-writes")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add-cut")
    s.add_argument("--group", required=True)
    s.add_argument("--label", required=True)
    s.add_argument("--asset-id", required=True)
    s.add_argument("--copy", required=True, help="the JSON review_copy.py printed, or a file holding it")
    s.add_argument("--source", help="path to the full-quality file, shown by the page's Show in Finder button")
    s.add_argument("--summary")
    s.add_argument("--qc")
    s.add_argument("--id")

    s = sub.add_parser("replies")
    s.add_argument("--file", required=True)
    s.add_argument("--new-cut", action="append", help="id of a cut being added in the same batch (repeatable)")

    s = sub.add_parser("chat-note")
    s.add_argument("--cut", required=True)
    s.add_argument("--text", required=True)
    s.add_argument("--author")
    s.add_argument("--when", help="ISO time the reviewer said it, if not now")
    s.add_argument("--only", action="store_true", help="applies to this cut only, not its siblings")

    s = sub.add_parser("checks")
    s.add_argument("--cut", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--new-cut", action="append")

    s = sub.add_parser("round")
    s.add_argument("--group", required=True)
    s.add_argument("--state", required=True, choices=["plan", "working", "done"])
    s.add_argument("--note")

    s = sub.add_parser("file")
    s.add_argument("--cut", required=True)
    s.add_argument("--source", required=True)

    a = ap.parse_args()
    db = load_db(a.db) if a.db and os.path.exists(a.db) else {"comments": {}, "videos": {}, "rounds": {}, "checks": {}}
    page = Page(db)
    out = Out(a.out)
    {"add-cut": cmd_add_cut, "replies": cmd_replies, "chat-note": cmd_chat_note, "checks": cmd_checks, "round": cmd_round, "file": cmd_file}[a.cmd](a, page, out)


if __name__ == "__main__":
    main()
