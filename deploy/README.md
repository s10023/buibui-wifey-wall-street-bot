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

## Off-site — the leg that survives losing the laptop

`deploy/backup-offsite.sh` (`make backup-offsite`, `make backup-offsite-dry-run`)
rclones `$WIFEY_BACKUP_ROOT` to `$WIFEY_BACKUP_REMOTE`. It snapshots nothing itself
— it syncs whatever the local leg already verified, so exactly one place decides
what a good snapshot is. `.env` is excluded from that tree on purpose: a credential
must not ride along to a third-party remote.

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

### Setup — what is done, and the one step that needs the operator

rclone is installed (v1.74.3, distro package at `/usr/bin` — **not** linuxbrew as in
the parent). Everything from step 2 on is outstanding, and **step 2 cannot be run
from a session**: the browser OAuth flow is interactive and hits EOF on the first
prompt through a `!` prefix.

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
output" rather than "don't print it". Redirect it.

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
— a fixture that could never reach the guard reports green either way.
