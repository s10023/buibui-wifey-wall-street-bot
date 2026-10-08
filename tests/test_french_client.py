"""Ken French daily-factor client: parser and zip tests (fixture-only, no network)."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from utils.french_client import (
    FRENCH_DAILY_URL,
    FrenchFormatError,
    csv_from_zip,
    fetch_daily_factors,
    parse_daily_factors,
)

# The layout of the real file (CRSP 202608 build): preamble, header, rows in
# percent, blank line, copyright trailer, CRLF endings. 1930-01-04 is a Saturday.
SAMPLE = (
    "This file was created by using the 202608 CRSP database.\r\n"
    "The Tbill return is the simple daily rate.\r\n"
    "\r\n"
    ",Mkt-RF,SMB,HML,RF\r\n"
    "19300103,    0.50,   -0.23,   -0.28,    0.01\r\n"
    "19300104,    0.70,    0.33,   -0.24,    0.01\r\n"
    "19300106,   -1.25,    0.10,    0.05,    0.02\r\n"
    "\r\n"
    "Copyright 2026 Eugene F. Fama and Kenneth R. French\r\n"
)


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


class TestParseDailyFactors:
    def test_percent_becomes_decimal_and_saturdays_survive(self) -> None:
        df = parse_daily_factors(SAMPLE)
        assert list(df.columns) == ["mkt_rf", "smb", "hml", "rf"]
        assert list(df.index) == [
            pd.Timestamp("1930-01-03"),
            pd.Timestamp("1930-01-04"),
            pd.Timestamp("1930-01-06"),
        ]
        assert df.index[1].dayofweek == 5  # the Saturday ^GSPC lacks
        assert df["mkt_rf"].tolist() == pytest.approx([0.005, 0.007, -0.0125])
        assert df["rf"].tolist() == pytest.approx([0.0001, 0.0001, 0.0002])

    def test_trailer_after_the_blank_line_is_not_parsed(self) -> None:
        assert len(parse_daily_factors(SAMPLE)) == 3

    def test_missing_header_is_refused(self) -> None:
        with pytest.raises(FrenchFormatError, match="header"):
            parse_daily_factors(SAMPLE.replace(",Mkt-RF,SMB,HML,RF", ",Mkt,SMB,HML"))

    def test_malformed_row_is_refused_not_skipped(self) -> None:
        # A skipped row would shorten the panel and read as data.
        bad = SAMPLE.replace("19300104,    0.70,", "19300104,    n/a,")
        with pytest.raises(FrenchFormatError, match="line 6"):
            parse_daily_factors(bad)

    def test_short_row_is_refused(self) -> None:
        bad = SAMPLE.replace(
            "19300104,    0.70,    0.33,   -0.24,    0.01", "19300104, 0.70"
        )
        with pytest.raises(FrenchFormatError, match="malformed"):
            parse_daily_factors(bad)

    def test_dates_running_backwards_are_refused(self) -> None:
        bad = SAMPLE.replace("19300106", "19300102")
        with pytest.raises(FrenchFormatError, match="increasing"):
            parse_daily_factors(bad)

    def test_header_with_no_rows_is_refused(self) -> None:
        with pytest.raises(FrenchFormatError, match="no data rows"):
            parse_daily_factors(",Mkt-RF,SMB,HML,RF\r\n\r\nCopyright\r\n")


class TestZipAndFetch:
    def test_single_csv_member_is_extracted(self) -> None:
        blob = _zip({"F-F_Research_Data_Factors_daily.csv": SAMPLE})
        assert csv_from_zip(blob) == SAMPLE

    def test_zip_with_two_csvs_is_refused(self) -> None:
        blob = _zip({"a.csv": SAMPLE, "b.csv": SAMPLE})
        with pytest.raises(FrenchFormatError, match="one CSV"):
            csv_from_zip(blob)

    def test_fetch_goes_through_the_injected_seam(self) -> None:
        seen: list[str] = []

        def fake(url: str) -> bytes:
            seen.append(url)
            return _zip({"F-F_Research_Data_Factors_daily.CSV": SAMPLE})

        df = fetch_daily_factors(fetch=fake)
        assert seen == [FRENCH_DAILY_URL]
        assert len(df) == 3
