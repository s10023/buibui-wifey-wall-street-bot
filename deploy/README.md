# Deploy — local backup of the research state

This directory holds the one operational concern this fork has: **keeping the
irreplaceable state alive**. There is deliberately no signal-watch daemon, timer,
or cron here — dispatch is the manual one-shot `make go-live`.

## What is at risk, and why git does not cover it

Two trees are single-copy and gitignored:

| Tree | Size | Why it cannot be rebuilt |
| --- | --- | --- |
| `analytics.db` | ~153MB | `signal_alert_outcomes` is the live out-of-sample ledger. yfinance will not re-serve a historical signal fire, and restarting the ledger yields a differently-*biased* sample, not an equivalent one. |
| `docs/plans/` | ~1MB | The whole research pipeline's output: the pundit ledger, the routing watermark, Streams A/B, the video notes, the parent-sync triage, the measurement scripts, the handoff. |

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
    MANIFEST.json         method, sizes, git commit, duckdb version, row counts
    docs/plans/...        the research tree, copied whole
    config/stocks.json
    .claude/settings.json
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
listed individually — `config/stocks.json` and `.claude/settings*.json`.

This diverges from the crypto parent, which runs an allowlist of individual
paths. The parent's own script records that list being found short **twice**:
*"an allowlist over a single-copy tree defaults to UNCOVERED"*. The cost argument
for an allowlist never applied here — the whole tree is ~1MB against a ~153MB
database — so this fork defaults to covered instead, and a file a future skill
invents is protected on the day it is written.

The one weak point of a denylist is the opposite: a large artifact dropped into
`docs/plans/` rides into every snapshot from then on. The script warns (never
fails) once that tree passes 50MB.

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

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/user/wifey-backup*.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wifey-backup.timer

systemctl --user list-timers 'wifey-*'      # confirm it is scheduled
journalctl --user -u wifey-backup -n 50     # read a run
systemctl --user start wifey-backup.service # fire one now
```

It fires at 08:10 and 13:10 UTC (16:10 / 21:10 MYT), offset 30 minutes from the
parent's backup timer so two large DuckDB exports do not run at once.
`Persistent=true` means a laptop that was asleep runs the missed fire once on
resume.

On failure — and only on failure — `OnFailure=` starts
`wifey-backup-alert.service`, which Telegrams the last 25 journal lines via
`deploy/notify-failure.sh`. That message body is raw journal output, which is
exactly the payload that used to break this alert: a traceback carries `line 33,
in <module>`, Telegram's HTML parser reads the bare `<` as an unclosed tag and
answers 400, and the alert failed on precisely the crashes it exists to report.
`utils/telegram.py` now drops `parse_mode` and retries as plain text on a 400.

## Off-site — the leg that is not built here

Not implemented in this fork: no `deploy/backup-offsite.sh`, no rclone dependency,
no remote. The layout above is deliberately a single directory so that port stays a
sync of `$WIFEY_BACKUP_ROOT` rather than a rewrite, and `.env` is already excluded
from the snapshot because a credential must not ride along to a third-party remote.

Upstream walkthrough: parent PRs **#582 / #587** (size, provider choice, rclone
setup) and **#631** (destination guards). Read #631 *before* writing the script
here — `rclone sync` mirrors deletions in **both** directions, and #582 / #587 only
guard the source. The parent's script refuses an empty source (no `MANIFEST.json`
is a fault, never "nothing to do"); nothing guarded the far side, where a mistyped
`*_BACKUP_REMOTE` mirrors the snapshot tree over the target and deletes the rest.

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

Operating rules, each of them learned upstream:

- **`rclone config delete` drops `root_folder_id`** — rotate a credential with
  `rclone config reconnect <remote>:`, since delete-and-recreate silently loses
  confinement.
- **Prove confinement, never assume it**: `rclone lsf <remote>:` must list the
  backup folder's *contents*, not the drive root. That one command is the whole
  proof.
- **The remote path is relative to the confined root** — `gdrive:snapshots`, not
  `gdrive:wifey-backups`, which would nest the folder name twice.
- **`--drive-use-trash=false` is load-bearing.** Drive Trash counts against quota
  and auto-empties only after 30 days while retention prunes about one snapshot a
  day, so the default parks GiB of dead snapshots against the quota while `rclone
  about` still reads healthy.
- **Never put a directory in a file array** — the `[ -f ]`-guarded loops skip it
  **silently**. The same gap has landed twice upstream.
- **Do not narrow the OAuth scope to `drive.file` as a "fix."** It can only touch
  files it created, so a rebuilt config cannot prune what the old one uploaded, and
  `sync` must be able to delete for retention to work at all.

⚠ **Re-derive the account question rather than inheriting the parent's answer.** The
parent runs on the operator's *personal* Google Drive — dedicated-account signup was
blocked on Google's phone verification — so its isolation is folder confinement plus
guards 2 and 3, not account separation. Whether that holds here depends on which
account this fork would use, and the answer belongs in this file rather than assumed
from upstream.

Tests would be the only gate: no shellcheck runs here and no CI step reads `deploy/`.
Two choices keep them from going vacuous. Every rejection asserts `sync` was **never
invoked** rather than merely that the exit was 1, because a script dying for an
unrelated reason also exits 1. And a **positive control** proves a populated *own*
destination still passes — without it, guard 3 would pass equally well if it rejected
any non-empty destination, which would break every sync after the first.
Mutation-test rather than trusting a green suite: disabling either tracked guard must
fail exactly its own test and nothing else.
