"""Ken French data-library client: the daily Fama/French 3-factor file.

The first non-yfinance price source, added for OV-1 (#418): it supplies the
total-return market (``Mkt-RF + RF``, the CRSP value-weighted index with
dividends) and the daily one-month T-bill rate that a price-only ``^GSPC`` and a
zero-yield cash leg cannot. Free, no key. Mirrors ``utils/edgar_client.py``: no
module-level side effects, the network is isolated to ``fetch_daily_factors``,
which takes the byte fetcher as a parameter, and the parser is pure.

Format facts, read from the 2026-09-24 build of the file (CRSP 202608):

- A free-text preamble, then a header row ``,Mkt-RF,SMB,HML,RF``, then one row
  per session ``YYYYMMDD, v, v, v, v`` with values in **percent**, then a blank
  line and a copyright trailer. CRLF line endings.
- Sessions include NYSE Saturdays until 1952, which yfinance's ``^GSPC`` does
  not carry. A caller joining the two must map onto this file's calendar rather
  than inner-join, or every pre-1952 Saturday return silently disappears.
- ``RF`` is printed to two decimals of a percent, so the daily rate is quantised
  to 1 bp (about 2.5%/yr per step).
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from collections.abc import Callable

import pandas as pd

FRENCH_DAILY_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_daily_CSV.zip"
)
_HEADER = ("Mkt-RF", "SMB", "HML", "RF")
_COLUMNS = ("mkt_rf", "smb", "hml", "rf")


class FrenchFormatError(ValueError):
    """The file no longer has the layout this parser was written against.

    Raised instead of returning a partial frame: a parser that skips rows it
    does not recognise turns a format change into a shorter panel, which reads
    as data rather than as a failure.
    """


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "wifey-research"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        data: bytes = r.read()
    return data


def csv_from_zip(blob: bytes) -> str:
    """The single CSV member of the library's zip, decoded.

    The file is ASCII today; ``latin-1`` decodes any byte, so a stray non-ASCII
    character in the preamble or trailer cannot crash the parse, and the data
    rows are validated by :func:`parse_daily_factors` regardless.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(members) != 1:
            raise FrenchFormatError(f"expected one CSV in the zip, found {members}")
        return zf.read(members[0]).decode("latin-1")


def parse_daily_factors(text: str) -> pd.DataFrame:
    """Parse the daily factor CSV into decimal returns indexed by session date.

    Returns columns ``mkt_rf, smb, hml, rf`` as decimals (the file's percent
    divided by 100) on a sorted, unique, tz-naive ``DatetimeIndex``. Raises
    :class:`FrenchFormatError` when the header is missing, a row between the
    header and the trailer is malformed, or dates repeat or run backwards.
    """
    lines = text.splitlines()
    try:
        start = next(
            i
            for i, line in enumerate(lines)
            if tuple(c.strip() for c in line.split(",")[1:]) == _HEADER
        )
    except StopIteration:
        raise FrenchFormatError(f"header row ,{','.join(_HEADER)} not found") from None

    dates: list[pd.Timestamp] = []
    rows: list[list[float]] = []
    for lineno, line in enumerate(lines[start + 1 :], start=start + 2):
        if not line.strip():
            break  # the blank line before the copyright trailer ends the table
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != 1 + len(_HEADER) or len(cells[0]) != 8:
            raise FrenchFormatError(f"line {lineno}: malformed row {line!r}")
        try:
            dates.append(pd.to_datetime(cells[0], format="%Y%m%d"))
            rows.append([float(c) / 100.0 for c in cells[1:]])
        except ValueError as exc:
            raise FrenchFormatError(f"line {lineno}: {exc}") from exc
    if not rows:
        raise FrenchFormatError("no data rows after the header")

    df = pd.DataFrame(rows, index=pd.DatetimeIndex(dates), columns=list(_COLUMNS))
    if not df.index.is_monotonic_increasing or not df.index.is_unique:
        raise FrenchFormatError("session dates are not strictly increasing")
    return df


def fetch_zip(
    fetch: Callable[[str], bytes] = _http_get,
    url: str = FRENCH_DAILY_URL,
) -> bytes:
    """The library's zip as bytes, so a caller can hash what it parsed."""
    return fetch(url)


def fetch_daily_factors(
    fetch: Callable[[str], bytes] = _http_get,
    url: str = FRENCH_DAILY_URL,
) -> pd.DataFrame:
    """Download and parse the daily factors. ``fetch`` is the network seam."""
    return parse_daily_factors(csv_from_zip(fetch_zip(fetch, url)))
