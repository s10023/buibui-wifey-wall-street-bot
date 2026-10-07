# Domain Docs

How the mattpocock engineering skills consume this repo's domain documentation. Layout:
**single-context**.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.
- **Research verdicts are this repo's ADRs for trading questions.** `CLAUDE.md` ("Sleeve
  verdicts", the TA detector and sweep freeze under "Fork lineage", and the "Footguns") and the
  Verdict section of each `docs/audits/*.md` record decisions not to re-litigate. A proposal
  that re-opens one must say so, as below.

If `GLOSSARY.md` or `docs/adr/` doesn't exist, **proceed silently**. Don't flag the absence and
don't create them upfront. The `domain-modeling` skill (reached via `/grill-with-docs` and
`/improve-codebase-architecture`) creates them lazily when terms or decisions get resolved.

## File structure

```text
/
├── GLOSSARY.md
├── docs/
│   ├── adr/            ← engineering decisions (hard to reverse, surprising, a real trade-off)
│   ├── audits/         ← research verdicts (written by the research workflow, not by ADRs)
│   └── superpowers/specs/  ← pre-registered sleeve and hypothesis designs
└── analytics/ …
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis,
a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary
explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing
language the project doesn't use (reconsider) or there's a real gap (note it for
`domain-modeling`).

## Flag ADR and verdict conflicts

If your output contradicts an existing ADR or a filed research verdict, surface it explicitly
rather than silently overriding:

> _Contradicts the `xsmom/` G3 FAIL verdict, but worth reopening because…_
