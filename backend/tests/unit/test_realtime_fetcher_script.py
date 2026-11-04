import io
import zipfile

import pandas as pd
import pytest
import httpx
import respx

from scripts.realtime_fetcher import (
    dedupe_against_recent,
    load_recent_event_ids,
    parse_events_zip_to_dataframe,
    parse_lastupdate_events_url,
    run_realtime_fetch,
)


def _build_zip_with_tsv(tsv_text: str) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("events.CSV", tsv_text)
    return data.getvalue()


def test_parse_lastupdate_events_url_success() -> None:
    txt = "123 http://x/mentions.csv\n456 http://x/export.CSV.zip\n"
    assert parse_lastupdate_events_url(txt) == "http://x/export.CSV.zip"


def test_parse_lastupdate_events_url_missing_events_line() -> None:
    txt = "123 http://x/mentions.csv\n"
    with pytest.raises(ValueError):
        parse_lastupdate_events_url(txt)


def test_parse_events_zip_to_dataframe_projects_columns() -> None:
    row = [""] * 61
    row[0] = "1001"
    row[1] = "20260318"
    row[2] = "202603"
    row[3] = "2026"
    row[7] = "IND"
    row[17] = "PAK"
    row[12] = "GOV"
    row[22] = "MIL"
    row[26] = "190"
    row[27] = "19"
    row[28] = "19"
    row[29] = "4"
    row[30] = "-8.5"
    row[31] = "15"
    row[32] = "3"
    row[34] = "-4.2"
    row[37] = "IND"
    row[45] = "PAK"
    row[53] = "IND"
    row[56] = "23.03"
    row[57] = "72.58"
    row[60] = "https://example.com"

    zipped = _build_zip_with_tsv("\t".join(row) + "\n")
    df = parse_events_zip_to_dataframe(zipped)

    assert len(df) == 1
    assert int(df.iloc[0]["GLOBALEVENTID"]) == 1001
    assert int(df.iloc[0]["SQLDATE"]) == 20260318
    assert df.iloc[0]["SOURCEURL"] == "https://example.com"


def test_realtime_manifest_follows_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.realtime_fetcher.fetch_csv_zip_to_df",
        lambda *args, **kwargs: pd.DataFrame(),
    )
    with respx.mock:
        respx.get("https://data.gdeltproject.org/gdeltv2/lastupdate.txt").mock(
            return_value=httpx.Response(301, headers={"location": "/gdeltv2/latest.txt"})
        )
        redirected = respx.get("https://data.gdeltproject.org/gdeltv2/latest.txt").mock(
            return_value=httpx.Response(200, text=(
                "1 hash https://example.com/export.CSV.zip\n"
                "1 hash https://example.com/mentions.CSV.zip\n"
                "1 hash https://example.com/gkg.csv.zip\n"
            ))
        )
        assert run_realtime_fetch() is None
        assert redirected.call_count == 1


def test_dedupe_against_recent_removes_existing_ids() -> None:
    df = pd.DataFrame(
        [
            {"GLOBALEVENTID": 1, "SQLDATE": 20260318},
            {"GLOBALEVENTID": 2, "SQLDATE": 20260318},
            {"GLOBALEVENTID": 3, "SQLDATE": 20260318},
        ]
    )

    deduped = dedupe_against_recent(df, {2, 3})
    assert deduped["GLOBALEVENTID"].tolist() == [1]


def test_load_recent_event_ids_returns_empty_when_no_parquet(tmp_path) -> None:
    assert load_recent_event_ids(str(tmp_path), max_rows=1000) == set()
