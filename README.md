# video-review

A skill for Claude that gives you a private, frame.io-style review page for your own videos, and has Claude make the changes you ask for.

You put a video up, leave notes pinned to the exact frame, press **Submit notes**, and Claude re-edits the video and puts up the next version with a list of what changed. Repeat until you approve one.

It works on any video: an ad, a reel, a rough cut, a talking-head clip, something Claude just made for you or something you exported from an editor.

## What you get

- **A review page in your own Claude account.** Private to you. It opens beside the chat.
- **Notes the way an editor expects them.** Pinned to a frame, across a range, on the whole video, or drawn on the picture.
- **A straight answer before any work.** For every note Claude says whether it can do it, can do part of it, or cannot, and why. It asks before judgement calls such as rewriting a line or choosing new footage.
- **A new version, never an overwrite.** Each round is v2, v3 and so on, with a "What changed" list you tick off and a side-by-side compare with the version before.
- **Checks before you see it.** Loudness against the last version, silent gaps, black frames, a frame sheet of every caption, and the spoken words against the script if you have Whisper installed. Plus a short list of things only your ears can confirm.
- **Approve** when a version is final, and **Show in Finder** to get at the full-quality file.

## What you need

- **Claude Code, or the Code tab of the Claude desktop app**, running on your own computer. The skill works on video files on your disk, so the ordinary chat window is not enough.
- **A Claude plan where Claude can publish artifacts.** The review page is one.
- **ffmpeg** (with ffprobe) and **Python 3**. Nothing else to install: the scripts use the Python standard library only.
- **Optional: Whisper** (`whisper-cli` from whisper.cpp with a model file, or `whisper`). Without it the spoken-word check is skipped and Claude tells you so.

## Install

Paste this into Claude:

```
Install the video-review skill for me: clone https://github.com/teyterudman1-debug/video-review-skill into ~/.claude/skills/video-review (make the skills folder if it does not exist). Then check that ffmpeg, ffprobe and python3 are installed and tell me how to install anything that is missing. Do not run anything from the repo.
```

Then start a new Claude session so it picks the skill up.

Prefer to do it by hand?

```bash
git clone https://github.com/teyterudman1-debug/video-review-skill ~/.claude/skills/video-review
```

Or download `video-review.skill` from the [latest release](https://github.com/teyterudman1-debug/video-review-skill/releases/latest). It is a zip: unzip it into `~/.claude/skills/` so you end up with `~/.claude/skills/video-review/SKILL.md`.

## Use

Open Claude in the folder your video project lives in, then just talk to it.

| You say | What happens |
|---|---|
| "Put this up for review" with a video file | The first time, Claude builds your review page and adds the video. After that it adds each new video to the same page. |
| You leave notes on the page and press **Submit notes** | Claude reads them, tells you what it can and cannot do, makes the changes and puts up the next version. Saying "apply my notes" in chat does the same. |
| "It's a bit slow" or "the music is too loud", in chat | Claude records it on the page as a note in your words, then treats it like any other note. |
| "v3 is approved" or the **Approve** button | That version is marked final. |

Claude will also offer the page by itself after it edits or renders a video for you.

## Update

Paste this into Claude:

```
Update the video-review skill: run git pull in ~/.claude/skills/video-review and tell me what changed.
```

Your review pages, videos and notes are not touched by an update. If you want the page itself to pick up new features, ask Claude to "rebuild the review page with the new template".

## Good to know

- **Your videos stay on your computer.** The page plays a small review copy (under 20 MB) that Claude uploads to your own private page. Nobody else can open the page unless you share it.
- **One page per project folder.** Claude keeps a small file called `.video-review.json` in the folder you work in. It records the page's link and where each video's full-quality file is. A different folder gets its own page.
- **What can be changed depends on what you have.** If the video was built by a script from clips, voice and music, almost anything can change. If you only have a finished file, Claude can cut, re-pace, fix loudness, add or cover captions, swap a shot and hold a frame, but it cannot change words that are spoken or remove something from the picture. It will tell you which is which before it starts.
- **The buttons that talk to Claude** (Submit notes, Approve plan, Show in Finder) reach Claude when a session has the page open with it. If none does, the page copies a one-line request for you to paste into chat, and nothing is lost.
- **Tested on macOS.** It is written to work on Windows and Linux as well, but those have had less use. If something breaks, open an issue.

## What it does on your computer

So you know before you install:

- runs `ffmpeg` and `ffprobe` on the videos you point it at, and writes new files next to them or in a scratch folder;
- runs the Python scripts in `scripts/`, which read and write local files only and make no network calls;
- uses Claude's own artifact tools to publish your private review page and upload the review copy to it;
- opens your file manager at a video when you press Show in Finder.

Everything is plain text in this repo. Read `SKILL.md` first if you want to see exactly what Claude is told to do.
