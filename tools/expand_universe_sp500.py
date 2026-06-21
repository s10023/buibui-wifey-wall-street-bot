"""One-shot: expand config/universe.json from S&P 100 to the current S&P 500.

Snapshots the pre-expansion stock symbols (the stable "mega" arm of experiment
#1's 2x2) to config/universe_sp100_snapshot.json, then merges the current S&P 500
constituents (+GICS sectors) as kind=stock/delisted=False members. Existing
entries (including ETFs and any listed dates) are preserved untouched.

Constituents source: by default scraped free from Wikipedia's "List of S&P 500
companies" via pandas.read_html; pass --from-csv PATH (columns: Symbol,Sector)
for a deterministic/offline run. Run once, then commit universe.json + snapshot
and re-run `make wifey-universe-backfill`.

Usage::

    PYTHONPATH=. poetry run python tools/expand_universe_sp500.py
    PYTHONPATH=. poetry run python tools/expand_universe_sp500.py --from-csv sp500.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

UNIVERSE_PATH = Path("config/universe.json")
SNAPSHOT_PATH = Path("config/universe_sp100_snapshot.json")


def merge_constituents(
    universe: dict[str, object],
    constituents: list[tuple[str, str]],
) -> tuple[dict[str, object], list[str]]:
    """Return (merged_universe, sp100_snapshot).

    Snapshot = the sorted pre-existing kind=="stock" symbols. New constituent
    symbols are added as kind=stock/delisted=False; existing members are left
    byte-identical (idempotent).
    """
    members: dict[str, dict[str, object]] = dict(universe["members"])  # type: ignore[arg-type]
    snapshot = sorted(s for s, m in members.items() if m.get("kind") == "stock")
    for symbol, sector in constituents:
        if symbol not in members:
            members[symbol] = {
                "sector": sector, "kind": "stock", "delisted": False,
            }
    merged = dict(universe)
    merged["members"] = members
    return merged, snapshot


def _load_constituents_from_csv(path: Path) -> list[tuple[str, str]]:
    import csv

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [(row["Symbol"].strip().upper(), row["Sector"].strip())
                for row in reader if row.get("Symbol")]


def _load_constituents_from_wikipedia() -> list[tuple[str, str]]:
    import urllib.request
    from io import StringIO

    import pandas as pd

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    # Wikipedia 403s the default urllib UA; send a browser UA. (read_html needs
    # an HTML parser, e.g. lxml; use --from-csv for a parser-free offline run.)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    table = pd.read_html(StringIO(html))[0]
    return [
        (str(s).strip().upper().replace(".", "-"), str(sec).strip())
        for s, sec in zip(table["Symbol"], table["GICS Sector"])
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-csv", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    args = parser.parse_args()

    universe = json.loads(args.universe.read_text())
    constituents = (
        _load_constituents_from_csv(args.from_csv)
        if args.from_csv
        else _load_constituents_from_wikipedia()
    )
    merged, snapshot = merge_constituents(universe, constituents)
    args.universe.write_text(json.dumps(merged, indent=2) + "\n")
    args.snapshot.write_text(json.dumps(snapshot, indent=2) + "\n")
    n_new = len(merged["members"]) - len(universe["members"])
    print(f"Merged {len(constituents)} constituents (+{n_new} new). "
          f"Snapshot: {len(snapshot)} S&P-100 stocks -> {args.snapshot}")


if __name__ == "__main__":
    main()
