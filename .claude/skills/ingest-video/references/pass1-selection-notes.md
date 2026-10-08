# Pass-1 selection notes

Supports `SKILL.md` steps 3 and 4.

## Hint config

Pure local config read — no API key, no network — so a hand-pasted URL resolves the same way
a polled one does. Returns `{"matched": …, "intro_recap_s": N, "item_cap": N, …}`. Run it per
video; a batch can span channels.

`hint` exits 1 if `config/youtube_channels.toml` does not exist (`load_feed_config` raises
`SystemExit` rather than returning `matched: false`). That file is gitignored and may
legitimately not exist here, since this skill's primary mode in this repo is a hand-pasted
URL with no follow list at all. A missing config is not an error condition for this step —
treat it exactly like `matched: false`, take both defaults, and carry on; that is what the
`|| true` is for.

Both knobs otherwise degrade quietly to a default: `matched: false` or `intro_recap_s: 0`
means no recap rule and nothing changes, and `item_cap` falls back to `video_marks.ITEM_CAP`
(12 here, not the parent's 5).

## Chapter window

In this repo the chapter window is the only trim that can fire — both channels in
`config/youtube_channels.toml` sit at `intro_recap_s = 0`. That means a false positive costs
more here: with no constant to fall back to, a wrongly-matched chapter is the whole trim. The
hint list is narrowed accordingly (`_RECAP_TITLE_HINTS` excludes `intro`) — an introduction
opens content, a recap replays prior calls, and only the second is what this window exists to
trim.

## Handle matching and item_cap

Note this keys on `meta.author` (an @handle), which matches neither the `UC…` id the poll
path uses nor a CJK display `name` — that is why `config/youtube_channels.toml` carries a
`handle` field. A channel with no `handle` never matches, and then both knobs silently take
their defaults. Neither degradation raises anything; the run just quietly keeps less.

In this repo the `item_cap` override is nearly inert: `ITEM_CAP` is already 12 and
`item_cap + len(TAIL_OFFSETS_S) <= FRAME_CAP` caps it at 13, so the usable range is 13..13,
and a larger value silently degrades kept items to `vision_confidence: "low"` (`yt_feed.py`
does not validate it). Treat a channel needing more as a reason to revisit the global
constants, not to set this key.

## Recap flags

`retrospective` drops a `setup` in code; `is_intro_recap` does not. `route_target` takes
`retrospective` and `rejected` keyword-only and returns `None` for a `setup` with either.

The flag only drops a `setup`. A `claim`/`mechanic` marked `is_intro_recap` is kept
deliberately, and no code drops it — the human reading step 7 is the entire mechanism there,
which is why 7c prints both flags. Step 8 must pass the suppressors explicitly; they default
to `False`, so omitting them fails open and silently.

## Tie-break and cutoff

Know which way the tie-break leans before reading a thin result: `keep_items` ranks by
specificity desc, then `ts` asc, so a tie at `ITEM_CAP` favors earlier material — a pundit who
opens with macro and closes with single names loses the single names first, sometimes
including the exact names in the video's own title. Read a thin yield as a cap artifact
rather than a video that made no calls, and check `dropped` before concluding otherwise.

Do not hand-roll the cutoff. The floor and the cap do different jobs and one number cannot do
both: the floor stops a thin video padding vibes up to the cap just because slots exist, the
cap bounds a dense one. Both live in code for the same reason `video_calltime.py` does — a
truncation rule stated only in prose drifts.

## Step 3 rationale

Pasting the array into the prompt
puts the whole transcript in your context for no gain.

A cap plumbed
everywhere except its one consumer is not plumbed.

## Step 4 rationale

Pinned by
`tests/test_video_calltime.py::test_cli_date_only_accepts_a_bare_date`.
