#!/usr/bin/env bash
# Off-machine leg of the backup: rclone the local snapshot tree to a remote.
#
# WHY THIS EXISTS SEPARATELY FROM backup-analytics.sh
# ---------------------------------------------------
# That script fixes the LIKELY failure (fat-finger delete, `git clean -xdf`, a
# bad script, a tool bug) by writing verified snapshots outside the repo. It
# explicitly does NOT fix the TOTAL failure -- disk death, laptop lost or stolen
# -- because every copy it makes lives on the same disk as the original.
#
# Two trees here are single-copy and NOT reconstructible:
#   analytics.db   holds `signal_alert_outcomes`, the live out-of-sample ledger.
#                  yfinance will not re-serve historical signal fires, so
#                  restarting collection yields a differently-BIASED sample
#                  rather than an equivalent one -- the rows are evidence, and
#                  evidence cannot be re-derived from a later tape.
#   docs/plans/    the entire research pipeline's output. It is gitignored, so
#                  `git ls-files docs/plans/ | wc -l` returns 0 and `git clean
#                  -xdf` deletes all of it with no prompt.
#
# So the local leg alone still loses everything to one hardware event.
#
# backup-analytics.sh shaped $WIFEY_BACKUP_ROOT so this leg is only ever an
# `rclone sync` -- dated directories, no in-place mutation, a MANIFEST.json per
# snapshot. This script is that sync and nothing more; it deliberately does no
# snapshotting of its own, so there is exactly one place that decides what a
# good snapshot is.
#
# SYNC, NOT COPY -- AND WHY THE RETENTION ORDER MATTERS
# -----------------------------------------------------
# `sync` mirrors deletions, which is what makes remote retention match local
# retention instead of growing without bound. The consequence to respect: a bug
# that empties $BACKUP_ROOT would, on the next run, empty the remote too. That
# is why this refuses to sync a source that has no snapshots in it -- an empty
# or missing backup root is treated as a fault, never as "nothing to do".
#
# Usage:  backup-offsite.sh [--dry-run]
#
# Env: WIFEY_BACKUP_REMOTE  (REQUIRED, e.g. "gdrive-wifey:snapshots")
#      WIFEY_BACKUP_ROOT    (default ~/backups/wifey -- same default as the local leg)
#      WIFEY_RCLONE_FLAGS   (optional extra flags, e.g. --bwlimit 2M)
#      WIFEY_OFFSITE_MAX_SNAPSHOT_DELETES (default 4; guard 4's per-run cap)
#
# WHAT STOPS THIS TOUCHING ANYTHING ELSE ON THE DRIVE
# ---------------------------------------------------
# The backup shares a Google account with the crypto parent's own snapshot tree,
# which this repo does not own and cannot restore. The primary separation is
# STRUCTURAL rather than procedural: wifey uses its OWN rclone remote
# (`gdrive-wifey`) pinned to its OWN folder, so rclone cannot navigate above it
# and even a bare `gdrive-wifey:` typo stays inside wifey's tree. The parent's
# `gdrive` remote is pinned to a different folder and is unreachable from here.
#
# ⚠ Do NOT "simplify" this to one shared remote with two paths. `gdrive` is
# pinned to the parent's folder, so `gdrive:wifey-snapshots` resolves INSIDE it
# rather than beside it, and one typo of the parent's path still deletes their
# backup. Two pinned roots turn that from care into cannot.
#
# Three guards then sit on top, deliberately at different layers:
#   1. rclone's own `root_folder_id`, pinned on the remote, so rclone resolves
#      every path relative to the backup folder and cannot address anything
#      above it. Strongest, but it lives in rclone.conf and is LOST when the
#      config is recreated -- which is what rotating a credential does.
#   2. a rejection here of any remote without a path component: a bare
#      `remote:` is the whole drive, and sync mirrors deletions into it.
#   3. a rejection here of a destination holding entries the local root does
#      not have, which catches a well-formed remote aimed somewhere unintended.
# Guards 2 and 3 are tracked code and survive a reclone; guard 1 does not. Keep
# all three -- each covers a failure the others do not see. They vet WHERE the
# sync goes; guard 4 (at the sync) caps HOW MUCH one run may delete.
#
# ⚠ WHAT GUARD 3 DOES *NOT* COVER, measured 2026-08-15 in the parent repo.
# It compares TOP-LEVEL entries only, so it cannot tell a SAME-SHAPED sibling
# from our own data: the parent's tree is `daily/` + `weekly/` exactly like this
# one, and a dry-run aimed at it passed every guard and reported deletions of
# the parent's own files. Guard 3 catches an UNRELATED destination (someone's
# Photos/); it does not catch a lookalike. Do not conflate the two.
#
# Guard 1 on a wifey-only root is what makes that moot HERE -- the parent's
# folder is not addressable from this remote at all. The hole is still real for
# any future third tree sharing a root, so keep this note with the guard.
#
# ONE-TIME SETUP, which is interactive and therefore not automatable here:
#   1. install rclone                     (sudo dnf install rclone)
#   2. create wifey's OWN remote, UNPINNED so it can still see the Drive root:
#        rclone config create gdrive-wifey drive client_id=<ID> \
#            client_secret=<SECRET> scope=drive
#      ⚠ ORDER MATTERS. The remote must exist BEFORE its folder, because a
#      pinned root cannot be escaped -- `rclone mkdir gdrive:../wifey-backups`
#      does not work, and that is the whole point of pinning.
#   3. give it its own folder at the Drive root, then pin it:
#        rclone mkdir gdrive-wifey:wifey-backups
#        rclone lsf gdrive-wifey: --dirs-only --format ip | grep wifey-backups
#        rclone config update gdrive-wifey root_folder_id=<ID> --non-interactive
#   4. PROVE the pin: `rclone lsf gdrive-wifey:` must list the folder's CONTENTS
#      (empty on a fresh install), never the Drive root. Not optional, and not
#      inferable from step 3 -- `config update` on an OAuth remote returns a
#      token-refresh state machine that LOOKS incomplete even when the write
#      landed, so lsf is the only thing that settles it.
#      REDO steps 3-4 after any `rclone config delete` / recreate, which
#      silently drops the pin. Prefer `rclone config reconnect <remote>:` when
#      rotating a credential; that one keeps root_folder_id.
#   5. put WIFEY_BACKUP_REMOTE=gdrive-wifey:snapshots in .env. The path is
#      RELATIVE to the pinned root, so it is `snapshots`, never `wifey-backups`
#      (which would nest the folder name twice).
#   6. only THEN enable the timer:
#      systemctl --user enable --now wifey-backup-offsite.timer
#
# ⚠ `rclone config create` DUMPS THE WHOLE REMOTE TO STDOUT on success --
# client_secret, access_token and refresh_token -- unasked and unwarned. Always
# redirect it (`>/dev/null`). Two live credentials reached session transcripts
# this way on 2026-08-15, the second AFTER everyone involved knew about the
# first, because the mitigation in play was "do not paste the output" rather
# than "do not print it". A rule that depends on a human noticing is not a
# control; the redirect is. Verified against a dummy non-OAuth remote, so it is
# the command's behaviour rather than anything about Drive.
#
# The INTERACTIVE `rclone config` wizard prints the same block and CANNOT be
# redirected -- hiding stdout would hide its prompts. Prefer the non-interactive
# form above; if the wizard is unavoidable, clear the scrollback afterwards.
#
# Rotating a leak: remove the grant at myaccount.google.com/permissions (a
# re-consent is NOT enough -- Google keeps ~100 live refresh tokens per
# client+user), then `rclone config reconnect` BOTH remotes, since they share
# one client_id. `reconnect` keeps root_folder_id; `config delete` drops it.
# The falsifiable check that the old grant really died is the "Access given on"
# timestamp -- a re-consent leaves the original in place, so only a NEW one
# proves removal. Safe verifications: `rclone lsf <remote>:`, `rclone about`.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

BACKUP_ROOT="${WIFEY_BACKUP_ROOT:-$HOME/backups/wifey}"
REMOTE="${WIFEY_BACKUP_REMOTE:-}"
DRY=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        *) echo "unknown argument: $a" >&2; exit 2 ;;
    esac
done

# Fail loudly rather than exiting 0. An unconfigured remote on an ENABLED timer
# is the silent-never-ran failure this whole file exists to prevent -- it would
# look green forever while protecting nothing. Configure first, enable second.
if [ -z "$REMOTE" ]; then
    echo "ERROR: WIFEY_BACKUP_REMOTE is unset." >&2
    echo "  Off-machine backup is NOT running. Set it in .env (e.g." >&2
    echo "  WIFEY_BACKUP_REMOTE=gdrive-wifey:snapshots) after \`rclone config\`," >&2
    echo "  or disable this timer: systemctl --user disable --now wifey-backup-offsite.timer" >&2
    exit 1
fi

# The remote must name a PATH inside the drive, never a bare `remote:`.
#
# `sync` mirrors deletions into its destination, so a destination of `gdrive:`
# IS the whole drive -- one missing path component turns "back up" into "delete
# everything that is not a snapshot". That is a single-character typo away, and
# when the backup lands on a drive holding anything else, the blast radius is
# data this repo does not own and cannot restore.
#
# rclone's own `root_folder_id` confines the remote far more strongly, and it is
# set. It is NOT sufficient on its own: it lives in rclone.conf, and recreating
# the config drops it -- which is exactly what rotating a leaked token does. This
# check is tracked code, so it survives both a reclone and a config rebuild.
case "$REMOTE" in
    *:*) : ;;
    *)
        echo "ERROR: WIFEY_BACKUP_REMOTE='$REMOTE' is not a remote." >&2
        echo "  Expected <remote>:<path>, e.g. gdrive-wifey:snapshots." >&2
        echo "  Without a colon rclone writes to a LOCAL directory, so there" >&2
        echo "  would be no off-machine copy while this job looked green." >&2
        exit 1
        ;;
esac
if [ -z "${REMOTE#*:}" ]; then
    echo "ERROR: WIFEY_BACKUP_REMOTE='$REMOTE' has no path component." >&2
    echo "  A bare 'remote:' is the ENTIRE drive, and sync MIRRORS DELETIONS," >&2
    echo "  so this would delete every file on it that is not a local snapshot." >&2
    echo "  Use e.g. '${REMOTE}snapshots'." >&2
    exit 1
fi

if ! command -v rclone >/dev/null 2>&1; then
    echo "ERROR: rclone is not installed, so no off-machine copy exists." >&2
    echo "  Install it (sudo dnf install rclone), then \`rclone config\`." >&2
    exit 1
fi

if [ ! -d "$BACKUP_ROOT" ]; then
    echo "ERROR: backup root $BACKUP_ROOT does not exist -- run backup-analytics.sh first." >&2
    exit 1
fi

# Guard the sync-mirrors-deletions hazard described above: a source with no
# verified snapshot is a fault, not an empty workload. MANIFEST.json is the
# local leg's own completeness marker, so counting manifests counts snapshots
# that actually finished rather than directories that merely exist.
manifests=$(find "$BACKUP_ROOT" -name MANIFEST.json -type f 2>/dev/null | wc -l)
if [ "$manifests" -eq 0 ]; then
    echo "ERROR: no MANIFEST.json under $BACKUP_ROOT -- refusing to sync." >&2
    echo "  Syncing now would mirror the empty tree and DELETE the remote copies." >&2
    exit 1
fi

# Never sync INTO data this script did not put there.
#
# The two checks above catch a MALFORMED remote. This one catches a well-formed
# remote pointing somewhere unintended -- a real folder that simply is not ours.
# `sync` would delete everything in it that has no local counterpart, and on a
# drive shared with anything else that is unrecoverable.
#
# ⚠ Its reach is TOP-LEVEL entries only, so it does NOT catch the crypto
# parent's tree: that shares this fork's `daily/` + `weekly/` shape, so nothing
# reads as an intruder. See the header. Path separation is the control there;
# this check is not.
#
# The allowed set is derived from the local root rather than hardcoded to
# daily/weekly, so a new tier added by backup-analytics.sh does not read as an
# intruder here. An absent or empty destination lists nothing and passes, which
# is what makes the first-ever sync work.
#
# The listing must SUCCEED for any of that to mean anything (parent #785). It
# used to discard lsf's stderr AND exit code, so an rclone that could not reach
# the remote at all listed nothing and the guard passed without having looked.
# Only rc 3 (directory not found) is an empty destination. Measured on this host
# (rclone v1.75.1, 2026-09-28): an absent folder returns 3, a missing remote 1.
lsf_err="$(mktemp)"
listing="$(rclone lsf "$REMOTE" 2>"$lsf_err")"
lsf_rc=$?
if [ "$lsf_rc" -ne 0 ] && [ "$lsf_rc" -ne 3 ]; then
    echo "ERROR: could not list $REMOTE (rclone lsf rc=$lsf_rc), so the destination" >&2
    echo "  guard cannot vouch for it -- refusing to sync. rclone said:" >&2
    tail -n 3 "$lsf_err" | sed 's/^/    /' >&2
    rm -f "$lsf_err"
    exit 1
fi
rm -f "$lsf_err"
unexpected=""
while IFS= read -r entry; do
    [ -z "$entry" ] && continue
    entry="${entry%/}"
    [ -e "$BACKUP_ROOT/$entry" ] || unexpected="${unexpected}  ${entry}
"
done <<EOF
$listing
EOF
if [ -n "$unexpected" ]; then
    echo "ERROR: $REMOTE holds entries this script did not create:" >&2
    printf '%s' "$unexpected" >&2
    echo "  Refusing to sync -- sync MIRRORS DELETIONS and would remove them." >&2
    echo "  Check WIFEY_BACKUP_REMOTE points where you think it does." >&2
    exit 1
fi

# Guard 4: cap what one run may delete, so deletions cannot run unattended.
#
# Guards 2-3 vet WHERE the sync goes; none vets HOW MUCH it removes. The first
# dry run after the host migration planned to delete 19 remote snapshots (the
# old laptop's history) and still printed "off-site backup OK".
#
# The unit is SNAPSHOTS TOUCHED, not files: a snapshot is ~300 files and grows
# with the memory tree, so a file cap would drift. Retention prunes by count, so
# a routine run touches at most 3 (one rotated daily, one rotated weekly, and
# today's snapshot when the local leg re-ran). The default of 4 adds one missed
# run of slack. Above the cap this refuses in --dry-run too, listing what would
# go, so a human reads the plan; a deliberate prune sets the override once.
#
# The plan is remote files minus local files. WIFEY_RCLONE_FLAGS filters can
# only shrink what sync deletes, so the count errs high. `--max-delete` then
# binds the real sync to the count vetted here: if rclone ever plans more than
# this diff saw, it stops at that many deletes and exits 7 rather than going on.
MAX_SNAPSHOT_DELETES="${WIFEY_OFFSITE_MAX_SNAPSHOT_DELETES:-4}"
case "$MAX_SNAPSHOT_DELETES" in
    ''|*[!0-9]*)
        echo "ERROR: WIFEY_OFFSITE_MAX_SNAPSHOT_DELETES='$MAX_SNAPSHOT_DELETES' is not a" >&2
        echo "  non-negative integer -- refusing rather than guessing a cap." >&2
        exit 1
        ;;
esac
lsf_err="$(mktemp)"
remote_files="$(rclone lsf -R --files-only "$REMOTE" 2>"$lsf_err" | tr -d '\r')"
lsf_rc=$?  # pipefail: tr exits 0, so this is rclone's rc
if [ "$lsf_rc" -ne 0 ] && [ "$lsf_rc" -ne 3 ]; then
    echo "ERROR: could not list $REMOTE recursively (rclone lsf rc=$lsf_rc), so the" >&2
    echo "  deletion cap cannot count the plan -- refusing to sync. rclone said:" >&2
    tail -n 3 "$lsf_err" | sed 's/^/    /' >&2
    rm -f "$lsf_err"
    exit 1
fi
rm -f "$lsf_err"
doomed="$(LC_ALL=C comm -23 \
    <(printf '%s\n' "$remote_files" | grep -v '^$' | LC_ALL=C sort -u) \
    <(cd "$BACKUP_ROOT" && find . -type f | sed 's|^\./||' | LC_ALL=C sort -u))"
doomed_files=0
doomed_snapshots=""
if [ -n "$doomed" ]; then
    doomed_files=$(printf '%s\n' "$doomed" | wc -l)
    # A snapshot is the first two path components (daily/2026-09-30); a file
    # shallower than that counts as its own unit.
    doomed_snapshots="$(printf '%s\n' "$doomed" \
        | awk -F/ '{ print (NF >= 3 ? $1 "/" $2 : $0) }' | LC_ALL=C sort -u)"
fi
n_doomed_snapshots=0
[ -n "$doomed_snapshots" ] && n_doomed_snapshots=$(printf '%s\n' "$doomed_snapshots" | wc -l)
if [ "$n_doomed_snapshots" -gt "$MAX_SNAPSHOT_DELETES" ]; then
    echo "ERROR: this sync would delete $doomed_files remote file(s) across" >&2
    echo "  $n_doomed_snapshots snapshot(s), above the cap of $MAX_SNAPSHOT_DELETES:" >&2
    printf '%s\n' "$doomed_snapshots" | head -n 25 | sed 's/^/    /' >&2
    echo "  Refusing -- sync MIRRORS DELETIONS and Drive's trash is bypassed." >&2
    echo "  If this prune is intended, re-run once with" >&2
    echo "  WIFEY_OFFSITE_MAX_SNAPSHOT_DELETES=$n_doomed_snapshots." >&2
    exit 1
fi

echo "off-site backup: $BACKUP_ROOT -> $REMOTE  ($manifests verified snapshot(s))"
echo "  plan deletes $doomed_files file(s) across $n_doomed_snapshots snapshot(s) (cap $MAX_SNAPSHOT_DELETES)"

# --checksum, not size+mtime: these are large immutable snapshot files, and an
# rclone re-upload triggered by clock skew alone would cost real bandwidth.
# --transfers 2 keeps a laptop's uplink usable while it runs.
flags=(--checksum --transfers 2 --stats-one-line --stats 30s --max-delete "$doomed_files")
[ "$DRY" -eq 1 ] && flags+=(--dry-run)
# shellcheck disable=SC2086  # WIFEY_RCLONE_FLAGS is intentionally word-split
rclone sync "$BACKUP_ROOT" "$REMOTE" "${flags[@]}" ${WIFEY_RCLONE_FLAGS:-}
rc=$?

if [ "$rc" -ne 0 ]; then
    echo "ERROR: rclone sync failed (rc=$rc) -- the off-machine copy is STALE." >&2
    exit "$rc"
fi

echo "off-site backup OK"
