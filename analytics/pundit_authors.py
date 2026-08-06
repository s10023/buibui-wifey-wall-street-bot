"""Author identity for the pundit ledger — pure, no IO.

Ported from parent `buibui-moon-trader-bot` #555. The code is verbatim; the
measurements below are this fork's own, because the parent's ledger and this
one share no rows and quoting upstream's counts here would be provenance
fiction.

`tools/pundit_score.py` groups on `author` and needs zero changes to work,
which is exactly why a wrong `author` is cheap to write and expensive to
detect: nothing crashes, the report simply reports two shorter track records
for one person, and both may fall under an `n` threshold either would clear.

Measured on `docs/plans/pundit-calls.jsonl` (19 rows) at port time, this fork
has **no active split** — 4 author keys, and `@`-stripping collapses none of
them:

    '@fenggemeigu'      13 rows
    '@benjaminjcowen'    4 rows
    'SailorManCrypto'    1 row
    'luckychartape'      1 row

So this is prevention, not a repair, and no published scorecard was wrong.

**The latent split is real and has a named trigger**, which is why it is worth
paying for before it fires: the ledger is already written with **two
conventions at once** — two keys carry a leading `@`, two do not. Nothing
enforces either. `SailorManCrypto` is the live case: it sits here bare, while
upstream the same person appears as `@SailorManCrypto`, so the next row routed
under the `@` form silently starts a second track record for someone this
ledger already tracks. `@fenggemeigu` is the exposure that matters most — it
is 13 of 19 rows and the channel is ingested on an ongoing cadence, so a
single bare `fenggemeigu` would halve the only author with enough history to
be worth scoring.

**Scope, stated honestly.** This handles the mechanical part of identity — a
leading `@` and stray whitespace are decoration, not identity. It does NOT
handle:

- **case variants** (`@fenggemeigu` vs `@Fenggemeigu`). X handles are
  case-insensitive, so these ARE the same account and would still split. Zero
  instances in this ledger today; folding case here would also lowercase every
  display name, so it is deliberately left to a curated roster rather than
  paid for now.
- **aliases and transliterations** — the parent records 三马哥 / 三码哥 / 萨玛哥
  as one person and 舒琴 arriving ASR-mangled from whisper. This fork ingests
  the same CN-language channels via `/ingest-video`, so it inherits the
  exposure without yet having the instances.
- **collisions**, where one string is several people.

Those need a curated name->handle roster, which this fork does not have (the
parent's `config/pundit_roster.toml` was not ported — no relay/attribution
subsystem exists here yet). A roster is the right home for all three and
subsumes the case problem, so this module stays mechanical on purpose: it
fixes what can be fixed without asserting who anyone is.
"""

from __future__ import annotations


def normalize_author(raw: str) -> str:
    """Canonical author key: strip whitespace and any leading ``@``.

    Idempotent — ``normalize_author(normalize_author(x)) == normalize_author(x)``
    — which is what lets it be applied at read time on both sides of the
    ledger/priors join without a data migration.

    An author consisting only of ``@`` or whitespace normalises to the empty
    string; callers already treat a falsy author as a skipped row, so this
    returns the stripped original in that case rather than inventing a key.
    """
    stripped = raw.strip()
    out = stripped.lstrip("@").strip()
    return out or stripped
