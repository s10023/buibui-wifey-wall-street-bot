#!/usr/bin/env bash
# Windows Task Scheduler entry point — the adapter for everything a systemd unit here
# declares and Task Scheduler does not.
#
# WHY THIS EXISTS RATHER THAN A POWERSHELL REWRITE
# ------------------------------------------------
# The work itself (`make go-live`, `deploy/backup-analytics.sh`, …) is already portable:
# it is `make`, POSIX shell, `rclone` and Python, all of which this host has. What is NOT
# portable is the unit file's declarative half — `WorkingDirectory=`, `EnvironmentFile=`,
# `Environment=PATH=`, `OnFailure=` and the journal. Each of those becomes one numbered
# section below, so a reader can check the unit against the wrapper line by line.
#
# ⚠ **This does NOT mirror the parent's `deploy/windows/job.sh`, and must not be
# re-synced from it.** That one is a thin shim over `deploy/run-job.sh` — 192 lines of
# healthchecks.io pinging, resume-from-suspend network gating and failure formatting.
# **wifey has no `run-job.sh`**, so the shim would delegate to nothing. The
# responsibilities live here instead, which is why this file is longer than its upstream
# counterpart rather than shorter.
#
# Usage (from a Task Scheduler action, via Git Bash):
#   bash.exe -lc "deploy/windows/job.sh <unit-label> -- <command...>"
#
# Per-job `Environment=` lines become an ordinary env prefix on that command line:
#   bash.exe -lc "CATCH_UP=1 deploy/windows/job.sh wifey-signal-watch -- make go-live"

set -uo pipefail

if [ "$#" -lt 2 ]; then
    printf 'usage: %s <unit-label> -- <command...>\n' "$0" >&2
    exit 64
fi

label="$1"
shift
[ "${1:-}" = "--" ] && shift

# --- 1. WorkingDirectory= -----------------------------------------------------------
#
# This file is <repo>/deploy/windows/job.sh, so the root is two levels up. An off-by-one
# here runs the whole job in the wrong directory and still reports success, because every
# command it wraps is happy to start somewhere else and find nothing to do.
cd "$(dirname "$0")/../.." || exit 1

# --- 2. EnvironmentFile=-.env -------------------------------------------------------
#
# Extracted to its own file so it can be tested directly — see `load-env.sh` for why it
# parses rather than sources, and for the CRLF case that silently corrupts every value a
# Windows editor touches.
# shellcheck source=deploy/windows/load-env.sh
. "$(dirname "$0")/load-env.sh"
load_env .env

# --- 3. Environment=PATH= -----------------------------------------------------------
#
# The units pin PATH because a systemd user unit inherits almost nothing. A Task
# Scheduler action inherits the SYSTEM path instead, which carries `make`, `git` and
# `rclone` but NOT the venv.
#
# Prepended, never appended: `yt-dlp` is held at a dated nightly on purpose, so a stale
# copy earlier on PATH would shadow the pinned one and the ingest media leg would
# silently probe a build the pipeline never runs.
#
# ABSOLUTE, not relative: a relative PATH entry resolves against whatever the current
# directory is when a subprocess spawns, so any `cd` downstream drops the venv back off
# the front.
#
# ⚠ `Scripts`, not `bin` — that is the whole Windows difference, and the same one that
# made `deploy/backup-analytics.sh` silently verify snapshots under the wrong interpreter.
if [ -d "$PWD/.venv/Scripts" ]; then
    PATH="$PWD/.venv/Scripts:$PATH"
    export PATH
fi

# --- 4. encoding --------------------------------------------------------------------
#
# Windows defaults a redirected stdout to the ANSI codepage (cp1252 here) and this repo's
# output carries em-dashes and ⚠ throughout.
#
# ⚠ NOT cosmetic. Measured here 2026-09-18: the test suite is 0 failed with PYTHONUTF8=1
# and 41 failed without it — 40 UnicodeDecodeError, 38 UnicodeEncodeError, every one a
# `'charmap' codec` failure. A scheduled job that hits one dies outright, and nothing
# here pings a healthcheck, so that death is silent until someone reads the log.
#
# Set HERE rather than per-task in `install-tasks.ps1` so it covers every job at once and
# takes effect without re-registering anything. The Makefile exports it too; this covers
# the jobs that do not go through `make`.
PYTHONUTF8=1
export PYTHONUTF8

# --- 5. the journal's stand-in ------------------------------------------------------
#
# `deploy/README.md` says of the Linux host: everything goes to the journal, which
# handles its own rotation, deliberately, so nothing grows an unmanaged file on a laptop.
# Windows has no journal and a Task Scheduler action's stdout goes NOWHERE, so on this
# host that same sentence would mean no record at all.
#
# One capped file per job keeps that promise on a host whose scheduler will not keep it.
LOG_DIR="${WIFEY_LOG_DIR:-logs}"
LOG_MAX_LINES="${WIFEY_LOG_MAX_LINES:-2000}"
mkdir -p "$LOG_DIR"
logfile="$LOG_DIR/$label.log"

printf '=== %s | %s | %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$label" "$*" >>"$logfile"

# NOT `exec`: the pipeline's left-hand status has to be read back, and an exec'd process
# has no shell left to read it.
#
# `${PIPESTATUS[0]}` rather than `$?`, for independence from two things: it does not rely
# on `pipefail` staying set at the top of this file, and it distinguishes "the JOB
# failed" from "TEE failed", which `pipefail` deliberately conflates — a full disk would
# otherwise be reported as a failed signal-watch run.
"$@" 2>&1 | tee -a "$logfile"
rc=${PIPESTATUS[0]}

if [ -f "$logfile" ]; then
    tail -n "$LOG_MAX_LINES" "$logfile" >"$logfile.trim" && mv "$logfile.trim" "$logfile"
fi

# --- 6. OnFailure=wifey-alert@%N.service ---------------------------------------------
#
# The units route a failure to `notify-failure.sh`; Task Scheduler has no OnFailure, so
# the wrapper calls it.
#
# ⚠ The notifier's own failure must NEVER change the job's verdict. A Telegram outage
# would otherwise turn a SUCCESSFUL backup into a failed task, which is the wrong
# direction: the operator would go looking at the backup. Hence `|| true`.
#
# ⚠ `$WIFEY_NOTIFY` is an INJECTION POINT, and it exists for the tests. The failure path
# is the half most worth testing and the only one that sends Telegram, so without a seam
# here every test of it would message the operator's real channels -- including the wife
# channel, which has a human audience. The default is the real notifier, so production
# behaviour is unchanged and the seam cannot silently disable alerting.
# ⚠ `${VAR-default}`, NOT `${VAR:-default}`. The `:` form substitutes the default when
# the variable is unset OR EMPTY, so `WIFEY_NOTIFY=""` — the obvious way to say "do not
# notify" — silently resolved to the REAL notifier. That is not hypothetical: it fired
# during this file's own test run and sent live Telegram messages to the operator's
# channel, from the guard written to prevent exactly that.
#
# Semantics now: UNSET = the real notifier (production). EMPTY = explicitly disabled.
NOTIFY="${WIFEY_NOTIFY-deploy/notify-failure.sh}"
if [ "$rc" -ne 0 ] && [ -n "$NOTIFY" ] && [ -x "$NOTIFY" ]; then
    "$NOTIFY" "$label" >>"$logfile" 2>&1 || true
fi

exit "$rc"
