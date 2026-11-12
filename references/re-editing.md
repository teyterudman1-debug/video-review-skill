# Making the changes

How to turn a set of review notes into the next version of a video. Read this before any
re-edit. It is written for ffmpeg plus a scripting language, which is what you have on almost
any machine; if the project has its own editing tool or pipeline, use that and keep the rules.

Contents

1. Two situations: a build script, or only a finished file
2. Rules for every re-edit
3. With a build script
4. With only a finished file: what can and cannot be changed
5. Recipes
6. Traps that waste a render
7. Sibling cuts
8. What to hand back

## 1. Two situations

**There is a build script** (the state file's `build` for the cut, or a script or project file
beside the render). The video was assembled from clips, voice, music and text by code. Every
kind of change is possible, because you can change an ingredient and render again.

**There is only a finished file.** Someone exported it from an editor, or the ingredients are
gone. Picture and sound are baked together. Some changes are easy, some are a patch, and some
are impossible without the original material. Say which is which before you start (that is the
"can, partly, cannot" step in SKILL.md).

If you are about to make a second round of changes to a finished file, stop patching and write a
small build script that produces the current version from the pieces you have. Patches on top of
patches lose quality each time and get hard to reason about.

## 2. Rules for every re-edit

- **A new version is a new file.** `name-v2.mp4`, then `name-v3.mp4`. Never render over a
  version the reviewer has seen; their notes are pinned to its frames.
- **A new version is a new build script too.** Copy `build_v2.py` to `build_v3.py` and change
  the copy. The old script must still reproduce the old file.
- **Work from the highest-quality material you have.** Never edit the review copy; it is a
  small preview. Edit the source named in the state file, or better, the original clips.
- **Change only what the notes ask for.** A re-edit that also "improves" things nobody
  mentioned gives the reviewer new problems to find and makes the "what changed" list a lie.
  If you see something else worth fixing, say so and let them decide.
- **Track where things move.** Any change in length shifts everything after it. Keep a time
  map from the old version to the new one as you go (see section 8).
- **Long renders run in the background** with their output going to a log, so a timeout does
  not kill them.

## 3. With a build script

1. Read the whole script before changing it. Find where each thing a note mentions comes from:
   the line of voiceover, the caption text, the clip and its in-point, the hold length, the
   music level.
2. Copy it to the next version. Change the output filename and any work directory first, so
   nothing from the old version is overwritten.
3. Make the changes note by note, and keep a list as you go: note id, what you changed, where it
   will land.
4. Reuse what did not change. Renders that regenerate everything (new voice takes, new
   generated shots) cost time and sometimes money. Cache by content, not by step.
5. Render every sibling cut the script produces.
6. If the script does not already write a timing file (each beat or caption with its start
   time), add that. It gives you `--at` times for `qc_check.py` and `at` values for the replies.

**New spoken lines.** If the voice is a person, you need a new recording from them: ask, and
say exactly which lines. If it is a text-to-speech voice, regenerate only the changed sentences
with the same voice and settings, and check each one with the transcript check, because TTS
mispronounces names and reads abbreviations its own way (spell them out the way they should be
said). New voice takes usually cost credits, so they belong in the plan.

## 4. With only a finished file

| The note asks for | Possible? | How |
|---|---|---|
| Cut something out, trim the start or end | Yes | Cut and join (5.1) |
| Faster or slower pace | Yes, within about 15% | Tempo change, pitch kept (5.2) |
| Tighter pauses | Mostly | Remove silences (5.3). Picture cuts with it, so it suits talking heads and voiceover, not music-led edits |
| Louder, quieter, music lower | Level yes; rebalancing voice against music only if they are separate files | 5.4 |
| Replace the music | Only if the voice exists as a separate file. Otherwise no |
| Add a caption, card, logo or end frame | Yes | Overlay (5.5) |
| Change a caption that is burned into the picture | Partly | Cover it with a box or card and put the new text on top (5.5). Clean removal needs the original shot |
| Replace a shot under the same sound | Yes | Overlay a new clip for that time window (5.6) |
| Hold a frame longer | Yes | Freeze and insert (5.7) |
| Reorder sections | Yes | Cut and join in the new order. Check the music does not jump at each join |
| Change words that are spoken | No, unless you can re-record or regenerate that sentence and the music under it is separate |
| Remove something from the picture | No. Offer a crop, a cover or a different shot |
| Sharper, higher quality | No. Ask for a better export |

When the honest answer is "no, unless", say what the "unless" is: the project file, the
original clips, the voice as a separate track, the music bed without the voice.

## 5. Recipes

`IN` is the source, `OUT` the new version. All of these re-encode; use `-c:v libx264 -crf 18
-preset medium -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart` unless the source
calls for something else, and keep the source frame rate.

### 5.1 Cut and join

To remove 12.0 to 15.5 seconds, keep two pieces and join them. Filter-based cutting is
frame-accurate; stream copy (`-c copy`) is not, because it can only cut on keyframes.

```bash
ffmpeg -i IN -filter_complex "
 [0:v]trim=0:12.0,setpts=PTS-STARTPTS[v0]; [0:a]atrim=0:12.0,asetpts=PTS-STARTPTS[a0];
 [0:v]trim=start=15.5,setpts=PTS-STARTPTS[v1]; [0:a]atrim=start=15.5,asetpts=PTS-STARTPTS[a1];
 [v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]" -map "[v]" -map "[a]" OUT
```

Put a short audio crossfade at a join that clicks: `acrossfade=d=0.04` between the two audio
pieces in place of the audio half of `concat`. Cut on a silence or a beat where you can.

### 5.2 Pace

`atempo` changes speed without changing pitch. Picture and sound must move together.

```bash
ffmpeg -i IN -filter_complex "[0:v]setpts=PTS/1.06[v];[0:a]atempo=1.06[a]" -map "[v]" -map "[a]" OUT
```

Up to about 1.08 is hard to notice on speech; past 1.15 it sounds rushed. With a build script,
speed up the voice alone and re-time the picture to it, which keeps motion natural. "A bit
slow" is more often long pauses than slow speech, so try 5.3 first or as well.

### 5.3 Tighten pauses

Find them, then cut each one down to a set length with 5.1.

```bash
ffmpeg -i IN -af silencedetect=noise=-40dB:d=0.35 -f null - 2>&1 | grep silence_
```

Leave 0.2 to 0.3 seconds at each; removing a pause entirely sounds like a jump cut. Keep longer
pauses where a line is meant to land. This only works when the gaps are really silent, so not
under a continuous music bed.

### 5.4 Loudness

Measure first: `ffmpeg -i IN -af ebur128=peak=true -f null -` and read the Summary (`I` is
integrated loudness in LUFS, and the true peak). About -14 LUFS suits social platforms.

Use a fixed gain and a limiter, which keeps the mix as it was:

```bash
ffmpeg -i IN -c:v copy -af "volume=3.2dB,alimiter=limit=0.89:level=disabled" -c:a aac -b:a 192k OUT
```

Be careful with `loudnorm`. In its default mode it changes gain over time, which pumps room
noise up in the gaps between sentences. Its two-pass linear mode avoids that but silently falls
back to the pumping mode when the peak or range will not fit. A fixed gain cannot surprise you.

With separate voice and music files, duck the music under speech and leave it up in the gaps;
a bed at about -18 to -22 dB below the voice reads as "there but not fighting".

### 5.5 Text, cards and covers

Check whether this ffmpeg can draw text at all: `ffmpeg -filters | grep drawtext`. Many builds
cannot. The method that always works is to make the text as a transparent PNG and overlay it:

```bash
ffmpeg -i IN -i caption.png -filter_complex "[0:v][1:v]overlay=0:0:enable='between(t,4.4,6.9)'[v]" -map "[v]" -map 0:a -c:a copy OUT
```

Make the PNG at the video's exact pixel size with whatever is to hand: Pillow
(`ImageDraw.text` with a `.ttf` font), or a small HTML page screenshotted by a headless browser
with a transparent background. Match the existing captions: read the frame sheet for their
font, weight, size, case, position and outline before you design a new one. Many overlays chain
with one `overlay` per image, or are composited into one image sequence first.

To change burned-in text, the PNG carries a cover as well: a solid or blurred box over the old
words with the new words on top. Say that this is a cover, because it never looks as clean as
the original shot retitled.

**When the font is missing.** If the existing captions use a font that is not installed, do
not download one unasked. Use the closest face you have, look at the result beside an existing
caption, mark the note `partly`, and ask for the font file or for a yes to fetch it. When you
render text through a browser, name the font family exactly: a generic or system name can fall
back to something quite different without any error.

Keep text inside the safe area. On vertical video for social feeds, leave roughly the top 12%
and the bottom 20% clear, because the app's own buttons and captions sit there.

### 5.6 Replace a shot, keep the sound

Scale the new clip to the frame, cut it to the window, and overlay it for exactly that window.

```bash
ffmpeg -i IN -ss 2.0 -t 3.5 -i new_clip.mp4 -filter_complex "
 [1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setpts=PTS-STARTPTS+40.7/TB[n];
 [0:v][n]overlay=enable='between(t,40.7,44.2)':eof_action=pass[v]" -map "[v]" -map 0:a -c:a copy OUT
```

Look at a frame from the middle of the window and one frame either side of each edge, to check
the new clip starts and ends where it should and nothing of the old shot flashes through.

### 5.7 Hold a frame

Grab the frame as a PNG, make a clip of it for the extra time (`-loop 1 -t 0.8`), and insert it
with 5.1. Extend the audio with the bed or with silence so the two stay in step.

## 6. Traps that waste a render

- **Phone footage that looks washed out.** Modern phones record HDR (HLG or Dolby Vision,
  10-bit, BT.2020). A plain conversion to normal video goes grey and flat. Check with `ffprobe`
  (`color_transfer=arib-std-b67` or `smpte2084`), and tone-map: with a build that has `zscale`
  and `tonemap`, use them; without, convert with a LUT made for HLG to Rec.709.
- **Sideways video.** Phone clips carry a rotation flag. ffmpeg applies it by default, but
  width and height from `ffprobe` are the stored ones. Swap them when the rotation is 90 or 270.
- **Variable frame rate.** Screen recordings and phone clips drift out of sync when cut. Force
  a constant rate on the way in: `-vf fps=30` (or the project's rate) before anything else.
- **Changing brightness changes colour.** `eq=brightness` shifts hue on saturated colours.
  Darken in RGB (`format=gbrp,lutrgb=...` or `colorlevels`) and look at a frame.
- **Compressors lift the noise in pauses.** A breath or throat-clear that was inaudible in the
  raw take can be clearly audible after dynamics processing. Cut each take tight to its words.
- **Speech-to-text is not a listener.** It writes what it expects to hear. It will "hear" a
  word that was bleeped, miss a mumble, and report a clean take as a wrong word. Use it to find
  places to listen, never as proof that the audio is right.
- **Sound effects and music from a clip.** Check what is actually on a clip's audio before
  using it as background; "room tone" is sometimes somebody's music.
- **Fonts.** A missing font falls back silently to a default. After rendering text, look at it.
- **Scratch space.** Intermediate files for a 90 second vertical video run to gigabytes. Clean
  up between versions, and write intermediates outside the project folder.

## 7. Sibling cuts

Siblings (a main cut and a seasonal one, a long and a short) share most of their material. Build
them from one script with a switch for the part that differs, so a fix can never land in one and
not the other. If you inherit siblings as separate finished files, apply each change to both and
check each separately: the same timestamp is not always the same moment once their lengths
differ.

A note marked for one cut only changes that cut alone.

## 8. What to hand back

By the end of the edit you should be holding, for each new version:

- the full-quality render, named with its version;
- the build script (or the exact commands, saved to a file) that made it;
- the spoken words as a text file, if anything is spoken;
- for each note: what you did, and the time in the new version where it shows;
- the time of every caption or changed moment, for the frame sheet.

Then go back to SKILL.md step 5 and check it before anyone else sees it.
