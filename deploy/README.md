# Deploy — keeping the state alive, and firing the daily scan

This directory holds this fork's operational concerns: **keeping the
irreplaceable state alive**, and **firing one signal scan each trading morning**.

⚠ **There is still no signal-watch *daemon*** — dispatch is a single `make go-live`
cycle. What changed on 2026-08-25 is that the cycle no longer has to be run by
hand: `wifey-signal-watch.timer` fires it Mon–Fri at 08:30 UTC. The unit is
`Type=oneshot`, so nothing is held open between fires. It is **opt-in like every
other unit here — nothing installs it** — and installing it is an operator
decision because Telegram goes out. See [Scheduled signal run](#scheduled-signal-run).

## What is at risk, and why git does not cover it

Three trees are single-copy and unreachable by git:

| Tree | Size | Why it cannot be rebuilt |
| --- | --- | --- |
| `analytics.db` | ~153MB | `signal_alert_outcomes` is the live out-of-sample ledger. yfinance will not re-serve a historical signal fire, and restarting the ledger yields a differently-*biased* sample, not an equivalent one. |
| `docs/plans/` | ~1MB | The whole research pipeline's output: the pundit ledger, the routing watermark, Streams A/B, the video notes, the parent-sync triage, the measurement scripts, the handoff. |
| the memory tree | ~1MB | `project_todo_master.md` (the single source of truth to-do, carrying the north star and gates G1–G4), `MEMORY.md` and ~70 topic files. It records *intent* — what was ruled out and why — which is the one thing no re-run reconstructs. |

⚠ **The memory tree lives OUTSIDE the repo**, at
`~/.claude-personal/projects/<repo-path-slug>/memory`. That is why it was
uncovered until 2026-08-18: `BACKUP_DIRS` and `BACKUP_FILES` are both resolved
against `$REPO`, so the "default to COVERED" denylist reasoning below only ever
applied *within* the repo, and anything above it was invisible by construction
rather than by judgement. It is now carried by the EXTERNAL ROOTS section.

`.gitignore:20` is the single line `docs/plans/`, written for scratch files
before the ingest pipeline was built to write into that directory. `git ls-files
docs/plans/ | wc -l` returns **0** — to git, none of it exists, so `git clean
-xdf` deletes all of it with no prompt.

`analytics.db.bak` in the repo root is **not** a backup: it is an undated,
unverified byte copy with no retention.

## Scope: this is the likely-failure leg only

It protects against a fat-finger delete, a `git clean -xdf`, a bad script, a
tool bug. It does **not** protect against disk death or a lost laptop — that
needs different hardware. `$WIFEY_BACKUP_ROOT` is deliberately shaped so that
leg is only ever an `rclone sync` of one directory to a remote.

## Usage

```bash
make backup           # snapshot; parquet export too if the newest is >=7 days old
make backup-dry-run   # report what would be captured, write nothing
```

Or call the script directly for the finer flags:

```bash
./deploy/backup-analytics.sh --weekly     # force the parquet export
./deploy/backup-analytics.sh --dry-run
```

Environment overrides:

| Variable | Default | Meaning |
| --- | --- | --- |
| `WIFEY_BACKUP_ROOT` | `~/backups/wifey` | Where snapshots land |
| `WIFEY_KEEP_DAILY` | `14` | Daily snapshots retained |
| `WIFEY_KEEP_WEEKLY` | `8` | Weekly parquet exports retained |
| `WIFEY_LOCK_RETRIES` | `10` | Attempts to wait out a DuckDB writer |
| `WIFEY_LOCK_SLEEP` | `30` | Seconds between those attempts |

The root is **not** shared with the crypto parent's `~/backups/buibui`. Both
repos write a file called `analytics.db`, so a shared root would have each
repo's `daily/<date>/` overwrite the other's.

## Layout

```text
~/backups/wifey/
  daily/2026-08-12/
    analytics.db          verified snapshot (repacked, ~11% smaller than source)
    MANIFEST.json         method, sizes, git commit, duckdb version, row
                          counts, and external_roots (path + file count each)
    docs/plans/...        the research tree, copied whole
    memory/...            the memory tree, copied whole from OUTSIDE the repo
    config/stocks.json
    signal_state.json     the candle watermark — repo ROOT, so no glob reaches it
    .claude/settings.json
    .claude/sensitive-terms.txt
  weekly/2026-08-12/
    parquet/              EXPORT DATABASE output — format-independent archive
```

## What the script guarantees

- **Verified.** `COPY FROM DATABASE` is checked row-count-for-row-count against
  the source inside the same connection, then the resulting file is re-opened
  standalone. A snapshot that cannot be opened is refused, not kept.
- **Never half-written.** Everything is built under a `.staging-$$` name and
  `mv`-d into place only after it verifies. A snapshot at the final path is
  therefore always restorable; there is no in-between state for a freshness
  check to mistake for success.
- **Not silently empty.** A snapshot reporting **0** `signal_alert_outcomes`
  rows is refused. That is the dangerous failure — a file that opens cleanly and
  holds none of the evidence.
- **Lock-tolerant.** If something else holds the database (`make go-live`,
  `make db-update`, a `make wifey-web` session), it retries with backoff and then
  falls back to a lock-free byte copy. It never kills the process holding the
  lock.

## Coverage is a denylist, not an allowlist

`docs/plans/` is copied **whole**, with build artifacts (`__pycache__`,
`.pytest_cache`) pruned afterwards. Only files living outside that tree are
listed individually — `config/stocks.json`, `.claude/settings*.json` and the gitignored
`.claude/sensitive-terms.txt`, whose absence would leave the pre-flip gate's own config
with no copy at all.

⚠ **`signal_state.json` is the member that was missed, and the miss is structural.** It is the
per-`(symbol, timeframe, strategy)` candle watermark and it sits at the repo **root**, so the
`docs/plans/` tree that covers every other watermark-shaped file — `yt-feed-state.json`,
`routed-ledger.json`, `processed.json`, `task-marks/` — cannot reach it. A denylist defaults to
covered only *within the tree it is applied to*, which is the same blind spot `EXTERNAL_ROOTS`
closed one level up.

⚠ **Its loss is silent in BOTH directions** — no error, and no burst of stale alerts either. The
crypto parent restored without it: every key came back cold, the cold-start guard then keeps
only the latest closed candle for an unwatermarked key, `--catch-up` replayed nothing, and
**three days of fires were lost permanently**. The OHLCV bars and the outcome resolutions both
recovered; only the fires depend on this file. Parent fix: #771.

This diverges from the crypto parent, which runs an allowlist of individual
paths. The parent's own script records that list being found short **twice**:
*"an allowlist over a single-copy tree defaults to UNCOVERED"*. The cost argument
for an allowlist never applied here — the whole tree is ~1MB against a ~153MB
database — so this fork defaults to covered instead, and a file a future skill
invents is protected on the day it is written.

The one weak point of a denylist is the opposite: a large artifact dropped into
`docs/plans/` rides into every snapshot from then on. The script warns (never
fails) once that tree passes 50MB.

### External roots — the denylist's other blind spot

A denylist defaults to covered only **within the tree it is applied to**. Both
`BACKUP_DIRS` and `BACKUP_FILES` resolve against `$REPO`, so the memory tree —
one directory further out — was uncovered by construction. `EXTERNAL_ROOTS`
closes that: entries are `label|absolute-path`, and `label` names the
destination **inside each snapshot**.

Inside, not beside, is the load-bearing part. It buys three properties for free:
the tree is published by the snapshot's atomic rename so it is never half-copied
under a name a freshness check trusts, it ages out under the same retention, and
`$BACKUP_ROOT`'s top level stays `daily/` + `weekly/` so the off-site leg mirrors
it with no change at all.

The default path is **derived** from `$REPO` (`/` → `-`) rather than hardcoded,
so a clone at another path resolves its own tree; `WIFEY_MEMORY_DIR` overrides it.

⚠ **`EXTERNAL_ROOTS` is an allowlist, and it inherits that shape's weakness** —
the one this repo rejected for in-repo coverage, on the grounds that *"an
allowlist over a single-copy tree defaults to UNCOVERED"*. A second tree outside
`$REPO` would be invisible until someone adds it, exactly as the memory tree was.
That is accepted rather than solved: outside the repo there is no bounded tree to
denylist *against*, so there is no "copy everything and prune" option to take.
What the design buys instead is **auditability** — `MANIFEST.json` names every
external root and its file count, so what is covered is readable from the artifact
rather than inferable only from the script. It does not tell you what is missing.
Adding a single-copy tree outside the repo therefore means adding it here too.
This is **not** the SoT's ruled-out "switching `make backup` to an allowlist":
in-repo coverage is still the wholesale denylist copy, unchanged.

An absent root **warns and is recorded as `files: 0` in `MANIFEST.json`** — it is
never fatal, because a fresh clone legitimately has no memory tree yet and
refusing the whole run over that would trade a real backup for none. The manifest
field is the point: a warning is read once, while `files: 0` is visible to every
later audit. ⚠ `research_files` deliberately **excludes** the external roots, so
that field keeps meaning what it meant in every earlier snapshot.

⚠ **The memory tree rides to the off-site remote**, since that leg syncs
`$BACKUP_ROOT` wholesale. It was scanned for credential *values* before being
added, and came back clean: every match was a variable *name* (`TELEGRAM_BOT_TOKEN_2`)
or prose *about* credentials, never a value. Re-run both legs if the tree ever
starts holding anything but notes — the second is the one that matters, since a
name scan alone would miss an unlabelled secret:

```bash
cd ~/.claude-personal/projects/<repo-path-slug>/memory
# 1. named-credential shapes
grep -rEin "refresh_token|client_secret|api[_-]?key *[:=]|bot_token|password *[:=]|\
BEGIN [A-Z ]*PRIVATE KEY|ghp_|sk-[A-Za-z0-9]{20}|AKIA[0-9A-Z]{16}" .
# 2. value shapes — telegram tokens, then any high-entropy blob
grep -rEn "[0-9]{8,10}:[A-Za-z0-9_-]{30,}" . | wc -l          # expect 0
grep -rEoh "[A-Za-z0-9+/=_-]{40,}" . | sort -u \
  | grep -vE "^[A-Za-z0-9_./-]*$"                              # expect paths/tickers only
```

Measured 2026-08-18: 0 token-shaped values; 123 unique long strings of which 122
are path- or word-like and the remaining one is a ticker list.

Deliberately **not** covered, so a future audit does not re-find them as misses:

- `.cache/` — 102MB and refetchable. Note this differs from the parent, where
  `.cache/chart-drops/processed.json` was the only record that a chart had been
  handled. This fork's routing record is `docs/plans/routed-ledger.json`, already
  inside the wholesale copy; everything under `.cache/` here is a fetch cache
  whose loss costs a re-download and nothing else.
- `.env` — live Telegram bot tokens. The backup root is shaped for an eventual
  off-site leg, and a credential is the one thing that must not ride along to a
  third-party remote.
- `analytics.db.bak` — backing up a backup doubles the snapshot for no added
  evidence.

## Restoring

```bash
# inspect before trusting
cat ~/backups/wifey/daily/2026-08-12/MANIFEST.json

# database
cp ~/backups/wifey/daily/2026-08-12/analytics.db analytics.db

# research tree (--no-clobber so an existing live file always wins)
cp -Rn ~/backups/wifey/daily/2026-08-12/docs/plans/. docs/plans/

# memory tree — note the destination is OUTSIDE the repo
cp -Rn ~/backups/wifey/daily/2026-08-12/memory/. \
  ~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/
```

From the weekly parquet export instead, when the `.db` will not open on a newer
DuckDB:

```bash
python -c "import duckdb; duckdb.connect('analytics.db').execute(\
\"IMPORT DATABASE '$HOME/backups/wifey/weekly/2026-08-12/parquet'\")"
```

## Optional: run it on a timer

The units are **not** installed by default and nothing in the repo installs
them. They are named `wifey-*` on purpose — the crypto parent's
`buibui-backup.service` and `.timer` are already present in this user's systemd
instance, and reusing the name would have one repo's unit shadow the other's.

**The full inventory, spelled out.** Every `cp` line below uses brace expansion,
so no literal unit filename appears in a command — a doc can enumerate by glob
and read as complete while naming nothing, which is how
`wifey-backup-offsite.service` went undocumented until the `/post-branch`
presence check caught it:

| Unit | Fires | Does |
| --- | --- | --- |
| `wifey-backup.service` | by `wifey-backup.timer` | local verified snapshot |
| `wifey-backup.timer` | 08:10 + 13:10 UTC | twice daily, `Persistent=true` |
| `wifey-backup-offsite.service` | by `wifey-backup-offsite.timer` | `rclone sync` to the remote |
| `wifey-backup-offsite.timer` | 13:55 UTC | once daily, after both legs above |
| `wifey-signal-watch.service` | by `wifey-signal-watch.timer` | one `make go-live CATCH_UP=1` cycle — sync, scan, Telegram, outcome backfill |
| `wifey-signal-watch.timer` | Mon–Fri 08:30 UTC | once each trading morning, pre-open, `Persistent=true` |
| `wifey-universe-sync.service` | by `wifey-universe-sync.timer` | one `make wifey-universe-sync` — incremental 4h/1d/1wk refresh of the 505 research members |
| `wifey-universe-sync.timer` | Sat 10:00 UTC | once a week, `Persistent=true` |
| `wifey-alert@.service` | `OnFailure=wifey-alert@%N.service` on any service above | Telegrams the last 25 journal lines **of the unit that actually failed** |

⚠ **The `cp` glob below is `wifey-*`, not `wifey-backup*`, and that is a fix.**
The narrower glob never matched `wifey-alert@.service`, so anyone rebuilding from
this recipe installed three units whose `OnFailure=` pointed at a fourth that was
not there — the alert would have failed to start at exactly the moment it was
needed. It works on the current box only because that file was copied by hand.
This is the same shape as the sentence above about enumerating by glob: a recipe
can look complete and still omit the thing it depends on.

⚠ **The alert is templated, and that is a correctness fix rather than tidiness.**
Its predecessor hardcoded `notify-failure.sh wifey-backup`, which was right while
one unit referenced it and silently wrong the moment a second did — an off-site
failure would have Telegrammed "wifey-backup FAILED" with the *local* backup's
journal attached. An alert that names the wrong unit and shows the wrong log is
worse than none, because it sends you to a healthy component. `%N` expands to the
failing unit's name and arrives in the template as `%i`.

⚠ **`OnFailure=` belongs in `[Unit]`.** In `[Service]` systemd logs "unknown key
… ignoring" and starts the unit anyway, so the alert is silently unarmed while
everything looks healthy — `wifey-backup.service` shipped that way until
2026-08-15. `systemctl start` cannot catch it; `systemd-analyze verify <unit>`
names the line, and `tests/test_systemd_units.py` now enforces it in `make test`
so nobody has to remember to run either.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/user/wifey-*.{service,timer} ~/.config/systemd/user/   # NOT wifey-backup* — see above
systemctl --user daemon-reload
systemctl --user enable --now wifey-backup.timer

systemctl --user list-timers 'wifey-*'      # confirm it is scheduled
journalctl --user -u wifey-backup -n 50     # read a run
systemctl --user start wifey-backup.service # fire one now
```

⚠ **`cp` copies, so a unit edited in the repo does NOT reach systemd until you
copy it again.** `ln -sf` from the repo instead if you would rather the installed
copy track the tracked one; either is fine, but pick one and know which you have —
a unit that fires stale content looks identical to one that fires current content.

It fires at 08:10 and 13:10 UTC (16:10 / 21:10 MYT), offset 30 minutes from the
parent's backup timer so two large DuckDB exports do not run at once.
`Persistent=true` means a laptop that was asleep runs the missed fire once on
resume.

On failure — and only on failure — `OnFailure=` starts
`wifey-alert@<failing-unit>.service`, which Telegrams that unit's last 25 journal
lines via `deploy/notify-failure.sh`.

⚠ **Failure is the ONLY signal, which makes the channel unfalsifiable.** Nothing
here emits a heartbeat, so a timer that silently stopped firing and a timer with
nothing to report are indistinguishable from the Telegram side. The parent closes
this with a `daily_check.py` off-site freshness line; wifey has no `daily_check.py`
at all.

**`make backup-check` is wifey's answer, and it closes the OBSERVATION half only.**
It reports the age of the newest verified snapshot, read from `captured_at_utc`
inside `MANIFEST.json` rather than from any mtime, so it measures the *input* this
leg copies rather than the copy's own exit code. ⚠ **It is a PULL, not a push** — it
answers the question when someone asks it, and nothing asks on a schedule, so a
timer that stops is still invisible until the next run. That is a strictly smaller
gap than "no way to tell at all", and it is not the same as closed. The unit-level
liveness check remains `systemctl --user list-timers 'wifey-*'`, which reports
whether a timer is *scheduled* — never whether its output is current. That message body is raw journal output, which is
exactly the payload that used to break this alert: a traceback carries `line 33,
in <module>`, Telegram's HTML parser reads the bare `<` as an unclosed tag and
answers 400, and the alert failed on precisely the crashes it exists to report.
`utils/telegram.py` now drops `parse_mode` and retries as plain text on a 400.

## Scheduled signal run

`wifey-signal-watch.{service,timer}` fire **one** `make go-live CATCH_UP=1` cycle
Mon–Fri at 08:30 UTC. Install and enable them exactly like the backup pair above;
⚠ **enabling this one sends Telegram**, so it is an operator decision rather than
a setup step.

**It runs `make`, not the CLI, on purpose.** The scheduled run is then literally
the documented hand-run, and the two cannot drift into different flag sets — the
failure `make backup`'s own recipe already names, where a hand-run and a timer
applied different retention with nothing reporting the difference. The cost is
that `ExecStart=/usr/bin/make go-live CATCH_UP=1` contains no in-repo path, so the
dangling-path check in `tests/test_systemd_units.py` has nothing to look at; a
companion check in that file pins the **target name** against the Makefile, which
is the rename this arrangement would otherwise leak to fire time.

**Why 08:30 UTC.** Three constraints, and it is the only slot that satisfies all
three. ⚠ **The first draft of this unit used 12:50 UTC, derived from constraints
1 and 2 alone** — which is how a fire time gets chosen without anyone asking
whether the machine is switched on.

1. **Market.** After the `1d` bar closes and before the bell. A `1d` bar stamps
   04:00 UTC (05:00 under EST) and forms for a full period, so post-close is not
   an option: at the bell the session's own daily bar has not closed. 08:30 clears
   the EST close by 3h30m — the tightest of the three margins — and the EDT bell
   by 5h, which is also the operator's lead time to act on an alert.
2. **Alert window.** It keeps the previous session's **first** 4h bar (13:30 UTC
   open, 17:30 UTC close) at 15h against `max_alert_age_hours = 24.0`, with 9h to
   spare. That bar is the one the recency window made deliverable at all — 120 of
   351 candles, 34%, were structurally undeliverable before it — so **a materially
   later fire ages it back out and silently restores the old rule.**
3. **Uptime.** This box is not always on, and `Persistent=true` recovers a missed
   *run* but never its alerts, so an hour the laptop is asleep through is a real
   cost rather than a deferral. Measured over 14 weekdays (2026-08-06→08-25) using
   the parent's 15-minute timer as an uptime record: **hour 08 UTC was up 14/14**,
   against 13/14 at 12:00 and 10/14 at 10:00. ⚠ **Re-measure before moving this** —
   the point is that the number is observed rather than assumed, and it decays.

   ```bash
   # count of distinct WEEKDAYS on which the box was up during each UTC hour
   journalctl --user -u buibui-signal-watch.service --utc -o short-iso --no-pager \
     | grep -o '^[0-9-]\{10\}T[0-9]\{2\}' | sort -u \
     | while IFS=T read -r d h; do
         [ "$(date -d "$d" +%u)" -le 5 ] && echo "$h"
       done | sort | uniq -c
   ```

   ⚠ **The weekday filter is not optional garnish** — drop it and the same
   pipeline returns 20/19/16 over calendar days. The ranking survives and the
   denominator does not, so the output still looks like a plausible answer to a
   question nobody asked. This unit fires Mon–Fri, so weekdays are the universe.

   ⚠ **The journal's own start is not a box-off.** It began 2026-08-06T08:51 UTC,
   so hours before that on day one read as absent when they were merely
   unobserved — which is why 05:00–07:00 score 13 rather than 14 and mean the same
   thing as hour 08. Check the window's first timestamp before reading a low hour
   as a pattern. ⚠ **This proxy dies if that parent timer is ever removed**, since
   it is the only dense fire record on the box; wifey's own unit fires once a day
   and can never measure this.

**Why Mon–Fri and not daily.** A weekend fire sees no new bars, and Friday's own
bars are day-filtered regardless. Nothing is lost by skipping it — ledger
recording does not depend on dispatch, so Friday's bars still land when Monday
fires.

⚠ **The Telegram leg is effectively Wed/Thu/Fri, and that is a consequence of
`day_filter = "tue_thu"`, not of anything in this unit.** A pre-open run reads the
*previous* session's bars and the filter suppresses on the bar's **open** weekday,
so the Monday fire (Friday bars) and the Tuesday fire (Monday bars) can never
alert. Keep them: they still do the sync, ledger and outcome-backfill work.

⚠ **`Persistent=true` recovers the RUN, never the alerts.** On a resume a day or
more late, every candle but the newest closed one has aged past the window, and
the newest is day-filtered like any other — the scan happens, the ledger fills,
and nothing is sent. **A timer fixes "forgot to run"; nothing fixes "the box was
off all week."** That is the residual gap, and it is smaller than the one this
unit closes rather than zero.

**It shares `analytics.db` with the backup leg, and runs second.**
`wifey-backup.timer` fires at 08:10 UTC — twenty minutes **ahead** of this unit,
not behind it. The snapshot is measured at 2.7s and worst-cases at five minutes on
its lock-retry path, so the lock is released well before the scan starts.
⚠ **That ordering is the whole argument, and it holds only on a normal day.**
Moving either time without re-deriving it puts a multi-minute scan in front of a
backup that then has only its own retry budget to wait it out. And **both units
set `Persistent=true`, so after a resume from suspend both missed fires are queued
together** — the 20 minutes are gone, and `RandomizedDelaySec` (120 / 60) spreads
them without guaranteeing order or non-overlap. `analytics/db_retry.py` and this
script's own lock retry carry that case, not the timetable. ⚠ It is also
*upstream's exact premise* for `db_retry` — unattended timers catching up
simultaneously after a resume — which this fork's docs described as unreachable
here until this unit existed. `TimeoutStartSec=900` then keeps a worst-case run (08:31
start, 15 min) clear of the parent's `buibui-daily-check` at 09:10 — a different
repo and a different database, so CPU contention only. The 900s is a **ceiling,
not a measurement**: this unit's real runtime is unmeasured, and the first fire is
the measurement.

```bash
systemctl --user list-timers wifey-signal-watch.timer   # scheduled?
journalctl --user -t wifey-signal-watch -n 50           # -t: the unit sets SyslogIdentifier
systemctl --user start wifey-signal-watch.service       # fire one now — ⚠ this SENDS
```

⚠ **`journalctl -u` returns nothing useful here.** journald tags entries with the
executable name, which is `make`, so without the unit's `SyslogIdentifier` the
lines land under another identity and `-u` reads empty while the output is sitting
in the journal. Use `-t wifey-signal-watch`.

⚠ **`buibui-signal-watch.*` in this same user instance is the crypto parent's**,
fires every 15 minutes, and now differs from wifey's by prefix alone. Read
`WorkingDirectory` before concluding anything from a unit name or a recent fire.

## Scheduled research-universe refresh

`wifey-universe-sync.{service,timer}` fire **one** `make wifey-universe-sync` a
week: an incremental 4h/1d/1wk sync of the 505 members in `config/universe.json`.
`Type=oneshot`, so this is not a daemon either.

⚠ **This unit exists because of a measurement, not a tidiness impulse.** Until
2026-08-26 nothing refreshed the research universe at all — `make go-live` syncs
the 13-symbol `config/stocks.json` watchlist, and the universe was a hand-run
`make wifey-universe-backfill` that nobody had a reason to remember. Measured that
day: **493 of 526 `1d` series had a last bar on or before 2026-06-18** (~10 weeks)
while the watchlist ran to the previous session. Nothing was broken; there was
simply no unit, which is why no `OnFailure=` alert could ever have said so.

⚠ **The cost lands on POOLED CROSS-SECTIONS, and it compounds an existing skew in
the same direction.** A breadth query mixes ~10-week-stale names with the fresh
watchlist, and the fresh set is exactly the mega-cap tilt `4h`'s 21% coverage
already carries. Two skews, one direction. `make freshness-check` reports it.

⚠ **SYNC, NOT BACKFILL — they are different targets and only one belongs on a
timer.** `wifey-universe-backfill` re-fetches from 2018 for 505 symbols × 3
timeframes; `wifey-universe-sync` asks each series for the tail after its own
newest bar (measured 2m15s for the whole universe against a 10-week-stale tree).
`sync` **refuses a symbol with no bars at all** and skips it, so a newly-added
constituent still needs the backfill once — that is why both targets exist. It is
also why 400 of 505 `4h` series stay absent: yfinance's intraday history window,
not staleness, and **this unit cannot close that 21% coverage gap.**

**Saturday 10:00 UTC**, for three reasons that all have to hold. It is the
longest-running writer on `analytics.db` (2m15s) and shares that file with two
other units — on a weekday it would sit between `wifey-backup` (08:10) and
`wifey-signal-watch` (08:30), putting a multi-minute writer in front of the signal
scan whose 20-minute separation is load-bearing and derived. Saturday removes the
signal unit entirely (it is `Mon..Fri`), leaving only backup, 1h50m away. Second,
`1wk` bars stamp on the week's Monday open and form until Friday's close, so a
Saturday run reads a **settled** week rather than a forming one. Third, nothing
downstream waits on this data — only a hand-run breadth study does.

⚠ **Weekend uptime here is UNMEASURED**, and that is the honest difference from
`wifey-signal-watch.timer`, whose 08:30 slot came from 14 weekdays of observed
uptime. `Persistent=true` carries the case, and it carries it *fully* here in a
way it does not for the signal unit: a late refresh loses nothing, because no
alert has a window to miss. `sync` fetches the tail after each series' own newest
bar, so lateness costs freshness, never data.

```bash
systemctl --user list-timers wifey-universe-sync.timer  # scheduled?
journalctl --user -t wifey-universe-sync -n 50          # -t: the unit sets SyslogIdentifier
systemctl --user start wifey-universe-sync.service      # fire one now — safe, sends nothing
```

⚠ **`make freshness-check` reads this timer's enabled state and changes what it
grades.** With the timer enabled the 505 members become a graded tier on a
weekly tolerance; without it they are reported as an absence. That is deliberate —
the units are opt-in, so "the universe has a cadence" is true on one box and false
on the next, and grading it unconditionally would print ~1,100 phantom faults on a
box that never installed anything.

⚠ **The pundit ledger still has no timer.** `make wifey-pundit-sync` stays a
hand-run before any ledger glance; see the note in `CLAUDE.md`. It is a third
universe with a third symbol convention (`^GSPC`, `GC=F`), and nothing refreshes
it.

## Off-site — the leg that survives losing the laptop

`deploy/backup-offsite.sh` (`make backup-offsite`, `make backup-offsite-dry-run`)
rclones `$WIFEY_BACKUP_ROOT` to `$WIFEY_BACKUP_REMOTE`. It snapshots nothing itself
— it syncs whatever the local leg already verified, so exactly one place decides
what a good snapshot is. `.env` is excluded from that tree on purpose: a credential
must not ride along to a third-party remote.

⚠ **A GREEN TIMER DOES NOT MEAN THE BACKUP IS CURRENT** (found 2026-08-17). Only the
off-site leg is scheduled. It mirrors `$WIFEY_BACKUP_ROOT`, and **nothing populates
that tree on a timer** — `make backup` is manual. So the nightly job logged
`off-site backup OK (4 verified snapshots)` while faithfully mirroring a tree frozen
two days earlier, and **the log line is byte-identical either way**. The success it
reports is its own, never the freshness of what it copied. **Run `make backup` before
`make backup-offsite`**, or the sync just re-affirms stale data.

The one visible tell is the **snapshot count failing to increment** across consecutive
runs — confirmed in the journal, which shows `(4 verified snapshot(s))` followed by
`off-site backup OK` on two successive nights:

```bash
journalctl --user -u wifey-backup-offsite.service -o cat | grep 'verified snapshot'
```

Even that is weak evidence, because the count is also legitimately flat on any day the
operator did not run the local leg — which is most days. It tells you the mirror is
stale; it cannot tell you whether that was intended. **`make backup-check` is the
stronger tell** and needs no journal: it dates the newest snapshot from its own
manifest, so "stale" and "nothing was due" stop looking alike.

This is the same shape as the pundit-ledger bug: a job reporting success about a
dependency that is silently not being produced. The transferable rule is that **a
scheduled job can only attest to the step it performs** — if a green light is to mean
"the data is current", something has to check the *input's* age, not the copy's exit
code. Two candidate fixes, and only one of them is a build. **Scheduling the local leg
needs no code**: `wifey-backup.{service,timer}` have been tracked since **#162** and only
wanted enabling (recipe above) — that is *before* the off-site leg (#205) and before the
sentence that called them unbuilt (#210), which is how the drift happened. They are also
already sequenced for exactly this — 08:10
/ 13:10 UTC against the off-site leg's 13:55. **Enabled on this machine 2026-08-19**,
after the journal showed `off-site backup OK` on 2026-08-16 with no `2026-08-16`
snapshot in the tree — the failure mode observed, not theorised. The second fix — have
the off-site leg refuse a source tree whose newest snapshot predates today — is still
unbuilt and still a user call; it is the belt-and-braces for the local timer running but
leaving a stale tree.
`systemctl --user list-timers 'wifey-*'` reports whether a timer is scheduled;
`make backup-check` reports whether its OUTPUT is current, which is the question
the failure-only alert cannot answer. Neither is a push heartbeat — both have to
be run by someone.

`sync` mirrors deletions in **both** directions. Source side is guarded upstream (no
`MANIFEST.json` is a fault, never "nothing to do"). The far side is what the guards
below exist for: a mistyped `WIFEY_BACKUP_REMOTE` mirrors the snapshot tree over the
target and deletes the rest.

### What actually runs here

| | |
| --- | --- |
| Remote | `gdrive-wifey:snapshots` — wifey's **own** rclone remote, not a path on the parent's |
| Pinned root | `wifey-backups`, pinned by folder **ID**, so renaming it in Drive is safe |
| Account | the operator's *personal* Google Drive, 100 GiB, shared with the crypto parent |
| Isolation | **structural** — `gdrive` is pinned to `buibui-backups`, `gdrive-wifey` to `wifey-backups`; neither remote can address the other |
| Timer | `wifey-backup-offsite.timer`, 13:55 UTC daily (opt-in, nothing installs it) |
| First sync | 228 files / 450 MB → **~5 min** |

⚠ **Two remotes, not one remote with two paths — and the difference is not
cosmetic.** `gdrive` is pinned to `buibui-backups`, so *every* path on it resolves
inside that folder: `gdrive:wifey-snapshots` would land **nested under the parent's
backup**, not beside it, and one typo of `gdrive:snapshots` would still delete the
parent's tree. A second remote pinned to its own folder makes that **impossible**
rather than merely discouraged — rclone cannot navigate above a pinned root, so even
a bare `gdrive-wifey:` stays inside wifey's folder. Same upgrade `root_folder_id`
bought in the first place: care becomes cannot.

**Sizing is per-FILE, not per-byte.** Measured in the parent 2026-08-15: 2.32 GiB
across 1,726 objects took ~32 min, and the first 68 objects carried 2.275 GiB in
~15% of the wall clock. Drive's per-file API overhead dominates, so estimate from
`find $WIFEY_BACKUP_ROOT -type f | wc -l`, not from `du`. wifey's 228 files are ~7.5×
fewer than the parent's, hence the ~5 min above. `--checksum` makes re-syncs
near-instant.

Provenance for the two upstream PRs this derives from: parent **#582 / #587** (size,
provider choice, rclone setup) and **#631** (destination guards).

### Setup — completed 2026-08-15; kept as the rebuild procedure

**This is done on this machine.** rclone v1.74.3 (distro package at `/usr/bin` —
**not** linuxbrew as in the parent), `gdrive-wifey` created and pinned, `.env` wired,
first sync verified byte-for-byte (228 objects / 471,037,868 bytes, remote == local),
timer enabled. The steps below are the procedure for a rebuild or a second machine.

⚠ **Step 2 cannot be run from a session** — the browser OAuth flow is interactive and
hits EOF on the first prompt through a `!` prefix. It needs a real terminal.

```bash
# 1. rclone installed                                        [DONE - v1.74.3]
sudo dnf install rclone

# 2. wifey's OWN remote, UNPINNED so it can still see the Drive root.
#    ORDER MATTERS -- the remote must exist before its folder, because a pinned
#    root cannot be escaped. The unverified-app interstitial is
#    Advanced -> Go to <app> (unsafe).
#
#    ⚠ THE `>/dev/null` IS LOAD-BEARING. `rclone config create` DUMPS THE WHOLE
#    REMOTE TO STDOUT ON SUCCESS -- client_secret, access_token AND
#    refresh_token -- with no flag asked for and no warning. It is not an error
#    path or a verbose mode; it is the normal output. That is how a live token
#    reached a session transcript twice on 2026-08-15. "Do not paste the
#    output" is not the fix, because it puts the burden on whoever is watching;
#    not printing it is.
#
#    Read the client out of the existing remote rather than by eye -- copying a
#    secret by hand is the other way it ends up on a screen:
CID=$(rclone config show gdrive | awk -F' = ' '/^client_id/{print $2}')
CSEC=$(rclone config show gdrive | awk -F' = ' '/^client_secret/{print $2}')
rclone config create gdrive-wifey drive \
    client_id="$CID" client_secret="$CSEC" scope=drive >/dev/null

# 3. Its own folder at the Drive root, then pin it BY ID
rclone mkdir gdrive-wifey:wifey-backups
rclone lsf gdrive-wifey: --dirs-only --format ip | grep wifey-backups
rclone config update gdrive-wifey root_folder_id=<FOLDER_ID> --non-interactive

# 4. PROVE the pin. Must print NOTHING (the folder is empty), never the Drive
#    root. Step 3's output cannot tell you this -- see the rules below.
rclone lsf gdrive-wifey:

# 5. Wire it in. The path is RELATIVE to the pinned root.
echo 'WIFEY_BACKUP_REMOTE=gdrive-wifey:snapshots' >> .env
echo 'WIFEY_RCLONE_FLAGS=--drive-use-trash=false' >> .env

# 6. Dry-run BEFORE the timer exists. Read the DELETE lines, not just the copies.
make backup-offsite-dry-run

# 7. First real sync by hand (~5 min at 228 files)
make backup-offsite

# 8. Only now install the timer
cp deploy/systemd/user/wifey-backup-offsite.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wifey-backup-offsite.timer
```

⚠ **`rclone config create` prints the whole remote — including `refresh_token` — to
stdout on success.** No flag requests it and nothing warns you. Two live credentials
reached session transcripts this way on 2026-08-15, the second *after* everyone
involved knew about the first, because the mitigation in play was "don't paste the
output" rather than "don't print it". Redirect it. Verified upstream against a dummy
non-OAuth remote (`rclone config create __leaktest alias remote=/tmp`), so the
behaviour is the command's, not something about Drive.

⚠ **The interactive `rclone config` wizard prints the same block and CANNOT be
redirected** — hiding stdout would hide the prompts you have to answer. So prefer the
non-interactive `config create` form above; when the wizard is unavoidable, clear the
scrollback afterwards rather than trusting yourself to scroll past it.

**Rotating a leaked token — both remotes, in this order:**

1. `myaccount.google.com/permissions` → the rclone app → **Remove all access**.
   Re-consenting is *not* enough: Google keeps ~100 live refresh tokens per
   client+user, so the old one survives a re-grant. The grant record must be removed.
2. `rclone config reconnect gdrive:` **and** `rclone config reconnect gdrive-wifey:`.
   ⚠ **Both**, because they share one `client_id` — revoking the app kills the
   parent's access too. `reconnect` preserves `root_folder_id`; **`config delete`
   drops it**, so never rotate that way.
3. Verify: "Access given on" must show a **new** timestamp, and `rclone lsf` on each
   remote must still be confined (`snapshots/` for the parent, empty for wifey).

Three guards, deliberately at different layers:

1. **`root_folder_id` pinned on the remote** — every path resolves relative to the
   backup folder and nothing above it is addressable. Strongest, and the only one
   **not** in version control.
2. **Reject a remote with no path component.** A bare `remote:` is the whole drive,
   one character from the correct value. Reject a value with no colon at all too:
   rclone would write **locally**, a green job with no off-machine copy.
3. **Reject a destination holding entries the local root does not have.** Derive the
   allowed set *from* the local root instead of hardcoding the snapshot tiers, so a
   tier added later is not read as an intruder.

Guards 2 and 3 are tracked code and survive a reclone; guard 1 does not. That is why
they duplicate it rather than trusting it.

⚠ **Guard 3 compares TOP-LEVEL entries only, so it cannot catch a same-shaped
sibling.** The parent's tree is `daily/` + `weekly/` exactly like this one; a
dry-run aimed at it passed every guard and reported deletions of the parent's own
files (measured 2026-08-15). It catches an *unrelated* destination — someone's
`Photos/` — which is a different failure. The two pinned roots above are what makes
this moot between these two repos; the hole is still real for any third tree sharing
a root, which is why `tests/test_backup_offsite_guards.py` pins it as an explicit
characterization test rather than leaving it as folklore.

Operating rules, each of them learned upstream:

- **`rclone config delete` drops `root_folder_id`** — rotate a credential with
  `rclone config reconnect <remote>:`, since delete-and-recreate silently loses
  confinement.
- **Prove confinement, never assume it**: `rclone lsf <remote>:` must list the
  backup folder's *contents*, not the drive root. That one command is the whole
  proof.
- **The remote path is relative to the pinned root** — `gdrive-wifey:snapshots`, not
  `gdrive-wifey:wifey-backups`, which would nest the folder name twice.
- ⚠ **Create the remote BEFORE its folder.** A pinned root cannot be escaped, so
  `rclone mkdir gdrive:../wifey-backups` does *not* work — that is the whole point of
  pinning. The new remote starts unpinned, makes its folder at the Drive root, and
  only then gets pinned.
- **`rclone config update` on an OAuth remote looks like it failed.** It returns a
  token-refresh state machine, so the write landing is not inferable from its output.
  `rclone lsf` is the only thing that settles it.
- **`--drive-use-trash=false` is load-bearing.** Drive Trash counts against quota
  and auto-empties only after 30 days while retention prunes about one snapshot a
  day, so the default parks GiB of dead snapshots against the quota while `rclone
  about` still reads healthy.
- **Never put a directory in a file array** — the `[ -f ]`-guarded loops skip it
  **silently**. The same gap has landed twice upstream.
- **Do not narrow the OAuth scope to `drive.file` as a "fix."** It can only touch
  files it created, so a rebuilt config cannot prune what the old one uploaded, and
  `sync` must be able to delete for retention to work at all.

### The account question, answered

This runs on the operator's **personal** Google Drive — dedicated-account signup was
blocked on Google's phone verification — so there is no account separation between
wifey, the crypto parent, and personal files. All the isolation there is comes from
the two pinned roots plus guards 2 and 3. The OAuth token still carries `scope=drive`
and would grant the whole account if it leaked; narrowing to `drive.file` is **not**
the fix, for the reason in the rules above. Moving to a dedicated account later costs
one `rclone config reconnect` and one re-sync.

⚠ **A credential leaked exactly this way on 2026-08-15** — `rclone config` output
pasted into a session transcript. It was rotated the same day. The falsifiable check
that a rotation *actually happened* is **"Access given on" at
`myaccount.google.com/permissions`**: a re-consent keeps the original timestamp, so
only a **new** one proves the old grant was removed and its refresh tokens
invalidated. Google keeps ~100 live refresh tokens per client+user, so re-consenting
alone does not kill the old one — this is the difference between "revocation was
requested" and "revocation happened", and it is answerable in ten seconds. Never
paste config output anywhere; `rclone lsf <remote>:` and `rclone about <remote>:` are
the safe verifications.

### Testing

The tests are the **only** gate: no shellcheck runs here and no CI step reads
`deploy/`. `tests/test_backup_offsite_guards.py` holds 9 cases, and three choices
keep them from going vacuous:

- Every rejection asserts `sync` was **never invoked**, not merely that the exit was
  1 — a script dying for an unrelated reason also exits 1.
- A **positive control** proves a populated *own* destination still passes. Without
  it, guard 3 would pass equally well if it rejected any non-empty destination, which
  would break every sync after the first.
- One test asserts a **hole** rather than a guard (`test_same_shaped_sibling_is_NOT_caught`),
  so guard 3's top-level-only reach is pinned rather than assumed.

**Mutation-tested 2026-08-15, not merely green**: disabling each of the four guards
in turn fails exactly its own test and nothing else. Re-run that after any edit here
— a fixture that could never reach the guard reports green either way. The runnable
form is `docs/plans/scripts/mutate_backup_offsite_guards.sh` (gitignored); it expects
4 mutations, each failing one test, and restores the script identical.
