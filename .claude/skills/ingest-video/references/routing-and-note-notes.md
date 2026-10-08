# Routing, digest and note notes

Supports `SKILL.md` steps 7, 8 and 9 and `## Guardrails`.

## Step 7: the board view is read-only

**The board view is read-only and strictly outside the routing path.** It must not write to a
sink, must not influence `content_type` / `verdict` / dedup, and must not feed anything
downstream (wifey has no daily Brief; the constraint is forward-looking). A narrative digest
of pundit opinion is exactly the thing that can become a trading input without ever earning a
track record, which is what `tools/pundit_score.py` exists to prevent. It is information, not
evidence — say so if it is ever quoted back as a reason to take a trade.

## Step 7: why summaries come first

**Why summaries come first.** Part of the reason this pipeline exists is to receive what is
in a video without watching it. The routing streams alone don't serve that goal — they
capture only what can be scored or tested, so an operator reading a routing table alone
learns nothing that cannot be scored. A batch of several pundits sharing a pivot zone, or
reading the same chart structure to opposite conclusions, is exactly the shape no routing
stream records.

## Step 7: Streams A and B scope

On Streams A and B, `all-entries` scope does not make an empty `candidates` list mean "not a
duplicate" — read it as "nothing scored above threshold." Both streams are `SEMANTIC_SINKS`,
so `semantic_scope` (`tools/route_dedup.py:540`) always returns `all-entries` and
`_comparable_entries` (`:550`) returns every entry in the file, so a near-verbatim
restatement is already in scope and can still rank below threshold. That is a lexical ranking
limit, not a scope gap, which is why the digest's human gate is doing the real work here — a
ranking miss becomes more likely as `mechanics-backlog.md` grows.

## Step 8: Stream C dedup scope

Stream C's near-duplicate exemption is across sources only: two pundits making the same call
are two real observations and `tools/pundit_score.py` scores both authors, so collapsing
those would delete signal. It never justified one video restating its own call, which is how
an entry leg and a target leg of a single position become two rows — so within one
`source_id` the pass does run (step 7's `same-source` scope plus the `pairs` call). Stream C
also still gets the exact `already_routed` block.

## Step 8: deep links

Blindly appending `&t=<ts>s` to a `youtu.be` URL produces `https://youtu.be/<id>&t=90s`,
which is broken — with no prior `?`, the `&` never starts a query string and the timestamp is
silently dropped by the player. Always branch on whether `"?"` is already in the URL before
appending.

## Step 8: invalidation levels

The trap it exists for: a level phrased as an invalidation ("unless it reclaims 29,200",
"跌破/站回 X 就反转", "invalidated above X") is a stop, never a `target`. Writing one into a
short's `target` puts the row instantly in profit, and the scorer books a `WIN` at ~0.00 R —
a fake statistic rather than a visible error. Note the guard's blind spot: a row stating that
one level and nothing else has no second leg to contradict it, so read the invalidation
phrasing yourself too — pass 2 translating a CN hedge clause is exactly where this slips
through.

## Step 9: note filename

**`video_id`, not a title slug — this is the rule, not a collision fallback.** The slug
pipeline keeps only `[a-z0-9]`, so a non-Latin title slugifies to the empty string and every
note from that channel would collapse onto one path, silently overwriting all but the last.
This repo's channels are mostly English, so the collision risk is lower here — but `video_id`
is unique by construction, is the key everything else in this flow is already filed under
(`.cache/video/<video_id>/`, the routing ledger, the YouTube deep link), and is what makes the
step-2b cross-repo check comparable across the two repos instead of guesswork.

A non-Latin handle collapses the same way — `meta.author` is usually the Latin `@handle`, but
not always, and then the name reads `<date>--<video_id>.md` with an empty author segment.
Ugly, still unique, still correct: do not "fix" it by putting the title back.

Some existing notes use an older `<title-slug>` filename form. Leave them — step 2b greps
frontmatter `video_id`, never filenames, so the two forms coexist safely.

## Step 7 rationale

The order is load-bearing, not cosmetic — see "Why
summaries come first" below.

A stated time can
move `call_ts_utc` up to `STATED_TS_MAX_LEAD_H` (168h) earlier than publish, and this
digest — specifically the human looking at it — is the only runtime control on that input;
`call_ts_utc (call_ts_source)` alone does not show the approver the quote that justified the
shift, so they cannot judge it. Showing the raw quote and the gap is what lets the approver
reject a fabricated or implausible timestamp before it reaches the ledger.

Bulk video ingest makes this the common case — a pundit routinely repeats
one thesis across a week of uploads.

Bare ones are dropped by `normalize_levels`, but a level written `2,050` is kept by design
and two entries can share it coincidentally.

`check` cannot catch these: every check runs before the approval that writes anything, so
when a video's items are checked none of them are on disk yet — two legs of one position are
only findable item-vs-item.

## Step 8 rationale

They are
keyword-only and default to `False`, so omitting them silently routes everything with no
error.

The scorer guards this (`_INVALID_SYMBOLS`) and skips
such a row with a "no symbol resolved" warning, but a skipped row is still a wasted write and
the guard is a backstop, not a licence to route unresolved items.

The zone
resolves to whichever edge price reaches first, so on a long it lands nearer and manufactures
an optimistic WIN, while on a short it lands further and strands the row OPEN — the error
runs in both directions and neither is safe. This is the same rule `/ingest-x` step 4 states;
both skills write this file, one parser scores both.

That blind spot is this pipeline's common case, not an edge case, so encode it by rule rather
than re-deriving one per run: a TA-narrating pundit commonly states one level and stops.

Inventing a plausible stop is worse than leaving the leg empty: it is unfalsifiable once it
is in the ledger, and unlike the fake-`WIN` above no guard can ever see it.

It
keeps its `/ingest-x` meaning (the pundit's verbatim hedging phrase) — this pipeline does not
extract that from a video, so the honest value is "not collected," not a repurposed
visual-corroboration score.

## Guardrails rationale

A subagent or
the orchestrator computing this by hand reintroduces the exact look-ahead defect the tool
exists to prevent (see the spec's "ledger-integrity constraint").

Bulk video ingest is what
makes this bite harder than single X posts, because a pundit repeats one thesis across a
week of uploads — and note the limit that follows from Stream C's `same-source` scope:
restatement by one author across two uploads is invisible to it, deliberately, since two
calls a week apart are two genuine observations the scorer resolves against different
bars.
