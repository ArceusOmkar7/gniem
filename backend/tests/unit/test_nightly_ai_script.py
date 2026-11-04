from pathlib import Path
import pandas as pd
import pytest
import httpx
import respx
import json
from backend.infrastructure.config.settings import Settings

from scripts.nightly_ai import (
    fallback_briefing,
    is_low_quality_briefing,
    load_gdelt_country_code_labels,
    sql_date_int,
    build_theme_category_counts,
    THEME_CATEGORY_MAP,
    GroqBriefingClient,
)


@pytest.mark.asyncio
async def test_openrouter_briefing_routes_through_free_model_fallbacks():
    client = GroqBriefingClient(api_key="test-key", model="google/gemma-4-26b-a4b-it:free",
        base_url="https://openrouter.ai/api/v1", fallback_model="nvidia/nemotron-3.5-super-vl:free")
    with respx.mock:
        route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "Metrics summary."}}]})
        )
        assert await client.generate_briefing("US", "United States", "10 events") == "Metrics summary."
        payload = json.loads(route.calls[0].request.content)
        assert payload["models"] == [client.model, client.fallback_model]
        assert "model" not in payload
        assert all(model.endswith(":free") for model in payload["models"])


@pytest.mark.parametrize("string_column", [False, True])
def test_theme_aggregation_preserves_priority_duplicates_and_window(tmp_path, string_column):
    hot = tmp_path / "hot"
    hot.mkdir()
    themes = ["TRADE", "GOVERNMENT", "ENERGY_TRADE", "unmatched", "trade", "TRADE"]
    rows = [
        {"SQLDATE": 20261102, "themes": themes},
        {"SQLDATE": 20261026, "themes": ["ECON_ELECTIONS"]},
        {"SQLDATE": 20261025, "themes": ["PANDEMIC"]},
        {"SQLDATE": 20261102, "themes": None},
        {"SQLDATE": 20261102, "themes": []},
    ]
    if string_column:
        for row in rows:
            if row["themes"] is not None:
                row["themes"] = " ; ".join(row["themes"])
    pd.DataFrame(rows).to_parquet(hot / "events.parquet", index=False)
    settings = Settings(gcp_project_id="test", hot_tier_path=str(hot), cache_path=str(tmp_path / "cache"))
    expected = dict.fromkeys(THEME_CATEGORY_MAP, 0)
    expected.update(POLITICS=2, ECONOMY=4)
    assert build_theme_category_counts(settings) == expected


def test_sql_date_int_uses_yyyymmdd_integer() -> None:
    from datetime import date

    assert sql_date_int(date(2026, 3, 19)) == 20260319


def test_fallback_briefing_contains_country_and_summary() -> None:
    text = fallback_briefing("IND", "Recent events: 42. Avg tone: -1.2")

    assert "IND" in text
    assert "Recent events: 42" in text
    assert "Geopolitical briefing" in text


def test_load_gdelt_country_code_labels_parses_tab_separated_file(tmp_path: Path) -> None:
    codes = tmp_path / "codes.txt"
    codes.write_text(
        "CODE\tLABEL\nUSA\tUnited States\nEUR\tEurope\n\nBADROW\n",
        encoding="utf-8",
    )

    loaded = load_gdelt_country_code_labels(codes)

    assert loaded["USA"] == "United States"
    assert loaded["EUR"] == "Europe"
    assert "BADROW" not in loaded


def test_is_low_quality_briefing_flags_generic_code_confusion() -> None:
    bad = "I am unable to determine the country code and could refer to many places."
    good = "Code USA shows elevated event volume and negative tone over the last week."

    assert is_low_quality_briefing(bad) is True
    assert is_low_quality_briefing(good) is False


def test_load_gdelt_country_code_labels_parses_two_letter_lookup_without_header(
    tmp_path: Path,
) -> None:
    codes = tmp_path / "lookup-countries.txt"
    codes.write_text(
        "US\tUnited States\nUK\tUnited Kingdom\nUP\tUkraine\n",
        encoding="utf-8",
    )

    loaded = load_gdelt_country_code_labels(codes)

    assert loaded["US"] == "United States"
    assert loaded["UK"] == "United Kingdom"
    assert loaded["UP"] == "Ukraine"
