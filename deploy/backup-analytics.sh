#!/usr/bin/env bash
# Local snapshot of the irreplaceable research state.
#
# WHAT THIS PROTECTS AGAINST, AND WHAT IT DOES NOT
# ------------------------------------------------
# Two single-copy trees, both gitignored:
#
#   analytics.db   ~160MB. Holds `signal_alert_outcomes` -- the live out-of-sample
#                  ledger. Those rows are NOT reconstructible: yfinance will not
#                  re-serve a historical signal fire, and restarting the ledger
#                  yields a differently-BIASED sample rather than an equivalent
#                  one (every current row was measured under the pre-#151 gate).
#   docs/plans/    ~1MB. The entire research pipeline's output: the pundit ledger,
#                  the routing watermark, Streams A/B, the video notes, the
#                  parent-sync triage, the measurement scripts, the handoff.
#
# `.gitignore:20` is the single line `docs/plans/`, written for scratch files
# before the ingest pipeline was built to write into that directory. To git,
# none of it exists -- so `git clean -xdf` deletes all of it with no prompt.
# `analytics.db.bak` in the repo root looks like a backup and is not one: it is
# an undated ad-hoc byte copy with no verification and no retention.
#
# This script fixes the LIKELY failure (fat-finger delete, bad script, a tool
# bug) by writing verified snapshots outside the repo. It does NOT fix the TOTAL
# failure (disk death, laptop lost/stolen) -- that needs different hardware, and
# this directory is deliberately shaped so that leg is only ever `rclone sync`
# of $BACKUP_ROOT to a remote.
#
# SNAPSHOT METHOD
# ---------------
# Primary is DuckDB's own `COPY FROM DATABASE`, not `cp`. The extra seconds buy a
# transactionally consistent read, which a byte copy cannot promise: `cp` of a
# live DuckDB can tear, and if a `.wal` sidecar exists at that instant the two
# files cannot be captured atomically by two separate `cp` calls.
#
# Byte copy survives only as the FALLBACK, because it takes no DuckDB lock and
# therefore still works when something else holds the database.
#
# LOCK CONTENTION IS POSSIBLE BUT NOT SCHEDULED
# ---------------------------------------------
# Unlike the crypto parent, this repo has NO signal-watch daemon, timer, or cron
# -- dispatch is the manual one-shot `make go-live`. So there is no fixed window
# to schedule around, and contention here comes from whatever the operator is
# running: `make go-live`, `make db-update`, or a `make wifey-web` session
# holding the DB open. On duckdb 1.5.5 a second PROCESS is refused even with
# read_only=True (only reader-vs-reader shares), so "open read-only to dodge the
# writer" does not work. Hence: retry with backoff, then fall back to the
# lock-free byte copy.
#
# NEVER kill a running scan to clear a lock here. It is the ledger writer; a
# backup must never cost you the thing it exists to protect.
#
# Usage:  backup-analytics.sh [--weekly | --weekly-if-due] [--dry-run]
#   --weekly         also EXPORT DATABASE to parquet (format-independent archive)
#   --weekly-if-due  do that only when the newest parquet export is >=7 days old
#   --dry-run        report what would happen, write nothing
#
# Env: WIFEY_BACKUP_ROOT (default ~/backups/wifey)
#      WIFEY_KEEP_DAILY  (default 14)
#      WIFEY_KEEP_WEEKLY (default 8)
#      WIFEY_LOCK_RETRIES (default 10)  WIFEY_LOCK_SLEEP (default 30s)

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
REPO="$PWD"

# Deliberately NOT the parent's ~/backups/buibui. The two repos share this
# script's SHAPE, not its storage: both write a file called `analytics.db`, so a
# shared root would have each repo's daily/<date>/ overwrite the other's.
BACKUP_ROOT="${WIFEY_BACKUP_ROOT:-$HOME/backups/wifey}"
KEEP_DAILY="${WIFEY_KEEP_DAILY:-14}"
KEEP_WEEKLY="${WIFEY_KEEP_WEEKLY:-8}"
LOCK_RETRIES="${WIFEY_LOCK_RETRIES:-10}"
LOCK_SLEEP="${WIFEY_LOCK_SLEEP:-30}"

DB="$REPO/analytics.db"

# COVERAGE IS A DENYLIST OVER A WHOLESALE COPY, NOT AN ALLOWLIST.
#
# The parent runs an allowlist of individual paths, and its own script records
# that list being found short TWICE -- "an allowlist over a single-copy tree
# defaults to UNCOVERED, so every new artifact is invisible until someone diffs
# the backup against the live tree". That failure mode is structural, not an
# oversight, so this fork does not inherit it.
#
# The cost argument for an allowlist never applied here anyway: all of
# `docs/plans/` is ~1MB against a ~160MB database. Copying the tree wholesale
# defaults to COVERED, which means a file a future skill invents is protected on
# the day it is written rather than on the day someone notices it is not.
BACKUP_DIRS=(
    "docs/plans"
)

# Single-copy gitignored files that live OUTSIDE docs/plans.
#   config/stocks.json      the 13-symbol live watchlist. `stocks.json.example`
#                           is a schema demo, not a backup -- losing this loses
#                           the hand-curated universe the daemon actually scans.
#   .claude/settings.json   machine-local (`.gitignore:12` is `.claude/*`) and
#                           holds the /post-branch PreToolUse hook. CLAUDE.md
#                           already carries a standing "re-add it after a
#                           reclone" note, i.e. this one has been lost before.
BACKUP_FILES=(
    "config/stocks.json"
    ".claude/settings.json"
    ".claude/settings.local.json"
)

# Pruned from the copy after the fact. Build artifacts only -- anything a tool
# regenerates for free. Keep this list SHORT: every entry is a decision that a
# future file matching it is worthless, and that is the judgement the parent's
# allowlist got wrong in the other direction.
PRUNE_GLOBS=(
    "__pycache__"
    ".pytest_cache"
)

# DELIBERATELY NOT COVERED, so the next audit does not re-find these as misses:
#   .cache/          102MB and refetchable. Note this DIVERGES from the parent,
#                    where `.cache/chart-drops/processed.json` was the only
#                    record that a chart had been handled. This fork's routing
#                    record is `docs/plans/routed-ledger.json`, which the
#                    wholesale copy above already covers; everything under
#                    .cache/ here is a fetch cache whose loss costs a re-download
#                    and nothing else.
#   .env             live Telegram bot tokens. $BACKUP_ROOT is shaped for an
#                    eventual off-site rclone leg, and a credential is the one
#                    thing that must not ride along to a third-party remote.
#   analytics.db.bak an undated, unverified byte copy of the database. Backing up
#                    a backup doubles the snapshot for no added evidence.

# Warn (never fail) if the wholesale copy stops being cheap. This is the one weak
# point of a denylist: a large artifact dropped into docs/plans is silently
# carried into every snapshot from then on. 50MB is ~50x today's size.
BACKUP_DIRS_WARN_MB=50

# The venv interpreter is named directly rather than via `poetry run` -- one less
# moving part on the minimal PATH a systemd user unit gets.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

want_weekly=0
weekly_if_due=0
dry_run=0
for arg in "$@"; do
    case "$arg" in
        --weekly)        want_weekly=1 ;;
        --weekly-if-due) weekly_if_due=1 ;;
        --dry-run)       dry_run=1 ;;
        *) printf 'backup-analytics.sh: unknown arg %s\n' "$arg" >&2; exit 2 ;;
    esac
done

stamp="$(date -u +%Y-%m-%d)"
now="$(date -u +%FT%TZ)"
final_dir="$BACKUP_ROOT/daily/$stamp"
weekly_dir="$BACKUP_ROOT/weekly/$stamp"

# EVERYTHING is built under a staging name and renamed into place only after it
# verifies. `mv` within one filesystem is atomic, so $final_dir either does not
# exist or holds a verified snapshot. There is no state in between for a
# freshness check to mistake for success -- which matters because the dangerous
# failure is not an absent backup but a present one that holds no evidence.
daily_dir="$BACKUP_ROOT/daily/.staging-$$"

log() { printf '%s\n' "$*"; }
die() { printf 'backup-analytics.sh: %s\n' "$*" >&2; exit 1; }

[ -f "$DB" ] || die "no database at $DB"

# --weekly-if-due: run the parquet export when the newest one is >=7 days old.
#
# Deliberately NOT `OnCalendar=Sun` in the timer. This is a laptop that is off or
# suspended a large part of the day -- a fixed weekday means a weekend away skips
# the weekly export entirely and nothing ever notices. Asking "is the newest one
# stale?" self-heals on whatever day the machine is next awake.
#
# Age comes from the directory's DATE-STAMPED NAME, not its mtime: mtime answers
# "when was this touched", which is a different question from "how old is the
# thing inside", and the two silently diverge.
if [ "$weekly_if_due" -eq 1 ]; then
    newest="$(find "$BACKUP_ROOT/weekly" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r | head -1)"
    if [ -z "$newest" ]; then
        want_weekly=1
    else
        newest_epoch="$(date -u -d "$(basename "$newest")" +%s 2>/dev/null || echo 0)"
        age_days=$(( ( $(date -u +%s) - newest_epoch ) / 86400 ))
        [ "$age_days" -ge 7 ] && want_weekly=1
    fi
fi

if [ "$dry_run" -eq 1 ]; then
    log "DRY RUN -- nothing will be written"
    log "  source     $DB ($(du -h "$DB" | cut -f1))"
    log "  daily  ->  $final_dir (staged, then renamed on verify)"
    [ "$want_weekly" -eq 1 ] && log "  weekly ->  $weekly_dir (parquet)"
    log "  retention  ${KEEP_DAILY} daily / ${KEEP_WEEKLY} weekly"
    for d in "${BACKUP_DIRS[@]}"; do
        if [ -d "$REPO/$d" ]; then
            log "  tree       $d ($(du -sh "$REPO/$d" | cut -f1), $(find "$REPO/$d" -type f | wc -l) files)"
        else
            log "  tree       $d -- ABSENT, will be skipped"
        fi
    done
    for f in "${BACKUP_FILES[@]}"; do
        if [ -f "$REPO/$f" ]; then
            log "  file       $f ($(du -h "$REPO/$f" | cut -f1))"
        else
            log "  file       $f -- ABSENT, will be skipped"
        fi
    done
    exit 0
fi

# Sweep staging dirs abandoned by earlier crashed runs. Safe to do unconditionally:
# a staging dir is by definition unverified, so nothing of value can be in one.
find "$BACKUP_ROOT/daily" -mindepth 1 -maxdepth 1 -type d -name '.staging-*' \
    -exec rm -rf {} + 2>/dev/null

mkdir -p "$daily_dir" || die "cannot create $daily_dir"
# A failure anywhere below leaves no trace at the final path.
trap 'rm -rf "$daily_dir"; rm -f "${err_file:-}"' EXIT

# --- snapshot the database ----------------------------------------------------
# Returns 0 on a clean COPY FROM DATABASE, 1 if the source lock was never free,
# 3 on a verification mismatch.
snapshot_clean() {
    "$PY" - "$DB" "$daily_dir/analytics.db" <<'PYEOF'
import sys, duckdb, json

src_path, dst_path = sys.argv[1], sys.argv[2]
hub = duckdb.connect(":memory:")
hub.execute(f"ATTACH '{src_path}' AS src (READ_ONLY)")
hub.execute(f"ATTACH '{dst_path}' AS bk")
hub.execute("COPY FROM DATABASE src TO bk")

# Verification runs inside the SAME connection that made the copy, so source and
# snapshot are compared against a consistent view rather than against a live file
# that may have gained rows in between. Any mismatch here is a real defect, not a
# race -- which is why this is an equality check and not a tolerance.
tables = [r[0] for r in hub.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_catalog='src' ORDER BY table_name").fetchall()]
counts, bad = {}, []
for t in tables:
    a = hub.execute(f'SELECT count(*) FROM src."{t}"').fetchone()[0]
    b = hub.execute(f'SELECT count(*) FROM bk."{t}"').fetchone()[0]
    counts[t] = a
    if a != b:
        bad.append(f"{t}: src={a} snapshot={b}")
hub.close()

if bad:
    print("VERIFY-FAIL " + "; ".join(bad), file=sys.stderr)
    sys.exit(3)
print(json.dumps(counts))
PYEOF
}

# Fallback: takes no DuckDB lock, so it works while a writer holds the file.
snapshot_bytes() {
    cp "$DB" "$daily_dir/analytics.db" || return 1
    # Copy the WAL when one exists; DuckDB replays it on open. Order matters --
    # db first, then wal -- so the wal is never older than the db it replays into.
    [ -f "$DB.wal" ] && cp "$DB.wal" "$daily_dir/analytics.db.wal"
    return 0
}

method=""
# `counts_json` looks dead and is not: capturing stdout is what keeps the
# verification JSON out of the log on every attempt. The manifest uses
# `verify_json` from the standalone re-open below instead, because that is the
# check the byte-copy path also gets. Do not "clean up" the assignment.
counts_json=""
# No trap here -- the EXIT trap set at staging time already removes this.
err_file="$(mktemp)"
for attempt in $(seq 1 "$LOCK_RETRIES"); do
    # Assign and capture rc on SEPARATE lines. Folding this into `if cmd; then`
    # loses the real exit code: a false if-condition with no else leaves $? at 0,
    # so the rc==3 branch below would be unreachable and a verification mismatch
    # would silently retry as though it were lock contention.
    counts_json="$(snapshot_clean 2>"$err_file")"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        method="copy-from-database"
        break
    fi
    # rc 3 is a verification mismatch, which retrying cannot fix.
    if [ "$rc" -eq 3 ]; then
        cat "$err_file" >&2
        die "snapshot verification FAILED -- source and copy disagree"
    fi
    log "database locked (attempt $attempt/$LOCK_RETRIES) -- retrying in ${LOCK_SLEEP}s"
    sleep "$LOCK_SLEEP"
done

if [ -z "$method" ]; then
    log "lock never cleared after $LOCK_RETRIES attempts -- falling back to byte copy"
    snapshot_bytes || die "byte-copy fallback also failed"
    method="byte-copy"
fi

# --- verify the snapshot opens standalone -------------------------------------
# The in-transaction check above proves the COPY was faithful. This proves the
# resulting FILE is independently openable -- the property that actually matters
# at restore time, and the only check the byte-copy path gets at all.
verify_json="$("$PY" - "$daily_dir/analytics.db" <<'PYEOF'
import sys, duckdb, json
try:
    c = duckdb.connect(sys.argv[1], read_only=True)
    tables = [r[0] for r in c.execute("SHOW TABLES").fetchall()]
    counts = {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    c.close()
    print(json.dumps(counts))
except Exception as e:
    print(f"OPEN-FAIL {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
)" || die "snapshot at $daily_dir/analytics.db does NOT open -- refusing to keep a backup that cannot be restored"

outcomes="$(printf '%s' "$verify_json" | "$PY" -c \
    'import sys,json; print(json.load(sys.stdin).get("signal_alert_outcomes","?"))')"

# A snapshot that opens cleanly and reports ZERO rows in the crown-jewel table is
# the dangerous failure: a file that looks like a backup and holds none of the
# evidence. Refuse to record it as a good one. `signal_alert_outcomes` is the
# right table to guard on because it is the one this repo cannot rebuild --
# OHLCV can be re-ingested from yfinance, backtest_runs re-swept.
[ "$outcomes" = "0" ] && die "snapshot has 0 signal_alert_outcomes rows -- that is a schema copy, not a backup"

# --- research trees -----------------------------------------------------------
# `cp -Rp "$src/."` copies the CONTENTS into an existing dir, so a repeated run
# cannot nest docs/plans/docs/plans. -p preserves mtimes, which the notes' own
# date-based filenames do not encode (the ingest date is in the name, the edit
# time is not).
for d in "${BACKUP_DIRS[@]}"; do
    if [ -d "$REPO/$d" ]; then
        size_mb=$(( $(du -sk "$REPO/$d" | cut -f1) / 1024 ))
        if [ "$size_mb" -ge "$BACKUP_DIRS_WARN_MB" ]; then
            log "WARNING: $d is ${size_mb}MB (>= ${BACKUP_DIRS_WARN_MB}MB) -- it is copied whole into EVERY snapshot; consider a PRUNE_GLOBS entry"
        fi
        mkdir -p "$daily_dir/$d"
        cp -Rp "$REPO/$d/." "$daily_dir/$d/" || die "could not copy $d"
        for g in "${PRUNE_GLOBS[@]}"; do
            find "$daily_dir/$d" -name "$g" -exec rm -rf {} + 2>/dev/null
        done
    fi
done

# --- individual files ---------------------------------------------------------
for f in "${BACKUP_FILES[@]}"; do
    if [ -f "$REPO/$f" ]; then
        mkdir -p "$daily_dir/$(dirname "$f")"
        cp -p "$REPO/$f" "$daily_dir/$f"
    fi
done

# --- manifest -----------------------------------------------------------------
# `files` is recorded so a later run can be diffed against an earlier one without
# re-reading the tree: a coverage regression shows up as a count that dropped.
tree_files="$(find "$daily_dir" -type f ! -name 'analytics.db*' | wc -l)"
{
    printf '{\n'
    printf '  "captured_at_utc": "%s",\n' "$now"
    printf '  "method": "%s",\n' "$method"
    printf '  "source": "%s",\n' "$DB"
    printf '  "source_bytes": %s,\n' "$(stat -c %s "$DB")"
    printf '  "snapshot_bytes": %s,\n' "$(stat -c %s "$daily_dir/analytics.db")"
    printf '  "research_files": %s,\n' "$tree_files"
    printf '  "git_commit": "%s",\n' "$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    printf '  "duckdb": "%s",\n' "$("$PY" -c 'import duckdb; print(duckdb.__version__)' 2>/dev/null || echo unknown)"
    printf '  "row_counts": %s\n' "$verify_json"
    printf '}\n'
} > "$daily_dir/MANIFEST.json"

# --- publish atomically -------------------------------------------------------
# Only now, with the snapshot verified and manifested, does it take the name a
# freshness check looks for. Replacing an existing same-day dir is deliberate:
# re-running on the same UTC date should refresh, and the incoming copy has
# already passed every check the outgoing one did.
rm -rf "$final_dir"
mv "$daily_dir" "$final_dir" || die "could not publish snapshot to $final_dir"
trap 'rm -f "${err_file:-}"' EXIT   # staging is gone; stop trying to remove it

log "daily snapshot ok  [$method]  $final_dir"
log "  signal_alert_outcomes = $outcomes rows"
log "  research files        = $tree_files"

# --- weekly parquet export ----------------------------------------------------
# A raw .db snapshot is hostage to the DuckDB storage format, which has broken
# across versions before -- a file archived today may not open on a future
# DuckDB. EXPORT DATABASE writes parquet + SQL instead: portable, compressible,
# and readable by anything, at the cost of being slower and losing DuckDB-native
# structure. Belt (fast daily .db) and braces (portable weekly parquet).
if [ "$want_weekly" -eq 1 ]; then
    mkdir -p "$weekly_dir"
    if "$PY" - "$final_dir/analytics.db" "$weekly_dir/parquet" <<'PYEOF'
import sys, duckdb
# Export from the SNAPSHOT, never the live file: the snapshot is already verified
# and takes no lock away from anything the operator is running.
c = duckdb.connect(sys.argv[1], read_only=True)
c.execute(f"EXPORT DATABASE '{sys.argv[2]}' (FORMAT PARQUET)")
c.close()
PYEOF
    then
        log "weekly parquet export ok  $weekly_dir/parquet ($(du -sh "$weekly_dir/parquet" | cut -f1))"
    else
        # Non-fatal: the daily .db snapshot is the primary artifact and it already
        # succeeded. Losing the portable copy is a degraded run, not a failed one.
        log "WARNING: weekly parquet export FAILED -- daily snapshot is still good"
    fi
fi

# --- retention ----------------------------------------------------------------
# Prune by count, oldest first. Directory names are UTC date stamps, so a plain
# reverse sort is chronological.
prune() {
    local dir="$1" keep="$2" label="$3" n=0
    [ -d "$dir" ] || return 0
    while IFS= read -r d; do
        n=$((n + 1))
        if [ "$n" -gt "$keep" ]; then
            rm -rf "$d" && log "pruned old $label snapshot $(basename "$d")"
        fi
    done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d | sort -r)
}
prune "$BACKUP_ROOT/daily" "$KEEP_DAILY" daily
prune "$BACKUP_ROOT/weekly" "$KEEP_WEEKLY" weekly

log "backup root now $(du -sh "$BACKUP_ROOT" | cut -f1) across \
$(find "$BACKUP_ROOT/daily" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) daily / \
$(find "$BACKUP_ROOT/weekly" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) weekly"
