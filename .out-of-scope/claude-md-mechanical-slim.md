# Mechanical slimming of CLAUDE.md

This repo does not run a mechanical trim of `CLAUDE.md`: no scripted removal of dated
narration, repeated phrasing or audit-trail sentences.

## Why this is out of scope

The parent repo priced the same cut (s10023/buibui-moon-trader-bot#751) and found no mechanical
slim to take: a ceiling of about 1.4% of the file, and 0.0% repeated 15-word shingles. What
reads as narration is usually the reason a rule is phrased defensively, so a cut that keeps
the rule and drops its clause invites the mistake the clause was written to stop. The saving is
small and the failure is silent, because a removed reason leaves no test red.

Hand edits that tighten a rule while keeping its reason are still welcome; this rejects only
the bulk, mechanical pass.

## When to reopen

Reopen if the always-loaded context budget actually binds (a session measurably losing
instructions or room to work because of the file's size). Then use the parent's guardrails: an
explicit allowlist of spans to cut, replace rather than delete, and run the `stale-anchors` leg
of `make post-branch-checks` afterwards.

## Prior requests

- #340: "Cut dated narration from CLAUDE.md (parent #751, priced and declined)"
