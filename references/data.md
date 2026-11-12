# What is stored on the review page

Everything is in the page's database, read and written with `ArtifactData`. Four collections.
`page_ops.py` builds every document you normally write; this file is for the cases it does not
cover and for reading documents you did not expect.

**One rule above all: never edit or delete a document the reviewer wrote.** Your side of the
conversation is new documents (replies, checks, cut rows). The only existing documents you
update are a `rounds` document (its state) and a `videos` row (approval, a removed review copy).

## `videos/<id>`: one row per version of a cut

| Field | Meaning |
|---|---|
| `group` | the video: the ad, the film, the episode |
| `label` | the cut and its version, as shown: "Main v2" |
| `variant` | the cut's name, "Main". Cuts with different names in one group are siblings |
| `version` | the version number, 2 |
| `prev` | id of the version before it, or null |
| `status` | `"review"` or `"approved"` |
| `approvedAt`, `approvedBy` | set when approved |
| `archived` | true when the reviewer has hidden the whole video from the list |
| `assetId` | the uploaded review copy; plays from `/_blob/<assetId>`. null once a replaced copy has been removed |
| `file` | full path of the full-quality file on the reviewer's computer, from `add-cut --source`. The page's Show in Finder button appears when it is set |
| `duration`, `w`, `h`, `fps` | from `review_copy.py` |
| `summary` | what changed in this version, one or two plain sentences |
| `qc` | `{ "ranAt", "items": [ { "name", "state": "ok" \| "look" \| "skip", "detail" } ] }` from `qc_check.py` |
| `createdAt`, `createdBy` | ISO time; the uploader's user id, or null when you added it |

The id is a slug (lowercase letters, digits, hyphens) and is also the page's `#link` to the cut.
Rows made before `variant` and `version` existed still work: the page reads both from the label.

## `comments/<id>`: notes and replies

| Field | Meaning |
|---|---|
| `videoId` | the version the note was left on |
| `parentId` | null for a note; the note's id for a reply |
| `t` | seconds from the start; null for a whole-video note |
| `tEnd` | end of a range in seconds, or null |
| `general` | true for a whole-video note |
| `variantOnly` | true when the note is for this cut only, not its siblings |
| `text` | the words |
| `strokes` | drawings: a list of strokes, each a flat `[x, y, x, y, …]` list in 0 to 1 coordinates from the top left of the frame; or null |
| `author` | the reviewer's opaque user id, `"claude"`, or `"chat"` |
| `via` | `"chat"` when you recorded feedback the reviewer gave in chat |
| `resolved`, `resolvedBy`, `resolvedAt`, `resolvedVia` | the reviewer's tick. `resolvedVia: "approve:<cut id>"` when approving a version ticked it |
| `editedAt` | set when the reviewer edited their note |
| `createdAt` | ISO time |

Replies written by you carry three more:

| Field | Meaning |
|---|---|
| `kind` | `can`, `plan`, `partly`, `cannot` or `done` |
| `addressedIn` | on `done` and `partly`: the id of the version the change is in |
| `addressedAt` | seconds in that version where the reviewer should look, or null |

**A note's state is worked out, not stored.** Resolved if `resolved` is true. Otherwise
addressed if it has a reply from you with `kind` `done` or `partly` and an `addressedIn`.
Otherwise open. That is why a `done` reply is all it takes to move a note to "changed, waiting
for your tick", and why you never touch the note itself.

Numbering on the page and in `notes_report.py`: within one version, notes at a frame are `#1`,
`#2`… in time order, and whole-video notes are `W1`, `W2`… in the order they were left.

## `rounds/<id>`: a submitted batch of notes

Created by the page when the reviewer presses Submit notes.

| Field | Meaning |
|---|---|
| `group` | the video |
| `videoId` | the version on screen when they pressed it |
| `noteIds`, `count` | the open notes at that moment |
| `state` | `submitted` → `plan` (you posted a plan that needs a go) → `approved` (they pressed Approve plan) → `working` (you are editing) → `done`. Or `withdrawn` if they took it back |
| `submittedAt`, `submittedBy`, `approvedAt` | times and who |
| `planNote` | one optional line you want shown beside the plan |

You move the state with `ArtifactData` `action: "update"` (read the document first for its
version; `page_ops.py round` prints the data). A round is a courtesy to the reviewer, so the
page can say what is happening. If there is no round because they asked in chat, skip it.

## `checks/<id>`: the check-by-ear list

| Field | Meaning |
|---|---|
| `videoId` | the version to listen to |
| `kind` | `"ear"` |
| `text` | what to listen for |
| `t` | seconds to jump to, or null |
| `order` | position in the list |
| `done`, `doneAt`, `doneBy` | the reviewer's tick |

## How the page reaches you

**Submit notes** and **Approve plan** try to send a comment to a Claude session that is watching
the page ("Apply my notes on <video>, <cut>…", "Go ahead with the plan on <video>, <cut>."). It
arrives as a turn headed as an artifact comment. Answer in that thread with `ArtifactComments`
(`reply`, then `resolve`) in one line, and do the work in the session. If no session is
watching, the page copies the same line for the reviewer to paste into chat. The round document
is written either way.

**Show in Finder** (Show in Explorer on Windows) copies the cut's `file` path and, when a session
is watching, sends "Show the file for <video>, <cut> in Finder (cut <id>)." SKILL.md step 9
says what to do with it. It writes nothing to the database.
