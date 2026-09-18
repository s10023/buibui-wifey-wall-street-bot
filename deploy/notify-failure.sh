#!/usr/bin/env bash
# Telegram the tail of a failed unit's journal. Wired via `OnFailure=` so it runs
# only when the unit it names actually failed.
#
# The message body is raw journal output -- which is exactly the payload that
# used to make this alert fail. A traceback carries `line 33, in <module>`, and
# Telegram's HTML parser reads that bare `<` as an unclosed tag and answers 400;
# before utils/telegram.py learned to drop parse_mode and retry as plain text,
# the alert failed on precisely the crashes it exists to report.
#
# Usage: notify-failure.sh <unit-name-without-.service>

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
REPO="$PWD"

unit="${1:?usage: notify-failure.sh <unit>}"

# Both venv layouts: `bin/` on POSIX, `Scripts/` on Windows. The fallback is `python3`,
# which EXISTS under Git Bash, so naming only the POSIX path does not fail here -- it
# silently runs the alerter under an interpreter that may not import `utils.telegram`,
# and the failure notification is the one thing whose own failure nobody sees.
PY="${WIFEY_PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in "$REPO/.venv/bin/python" "$REPO/.venv/Scripts/python.exe"; do
        [ -x "$candidate" ] && PY="$candidate" && break
    done
fi
[ -n "$PY" ] && [ -x "$PY" ] || PY="python3"

# `|| true` on the whole pipeline: an alerter that itself exits non-zero just
# adds a second failed unit to the journal and tells the operator nothing.
tail_lines="$(journalctl --user -u "${unit}.service" -n 25 --no-pager -o cat 2>/dev/null || true)"

# Windows has no journal, so `deploy/windows/job.sh` tees each job to `logs/<unit>.log`
# instead -- that file IS the journal's stand-in and is where the tail has to come from.
# Without this the alert still FIRES and still says FAILED, carrying
# "(no journal output captured)" as its entire body: an alert with no diagnostic, on a
# host where four of the four jobs have no other observable.
if [ -z "$tail_lines" ]; then
    logfile="${WIFEY_LOG_DIR:-$REPO/logs}/$unit.log"
    [ -f "$logfile" ] && tail_lines="$(tail -n 25 "$logfile" 2>/dev/null || true)"
fi

[ -n "$tail_lines" ] || tail_lines="(no journal or log output captured)"

"$PY" - "$unit" "$tail_lines" <<'PYEOF' || true
import sys
from utils.telegram import send_telegram_message

unit, tail = sys.argv[1], sys.argv[2]
send_telegram_message(f"❌ {unit} FAILED\n\n{tail[-3000:]}")
PYEOF
