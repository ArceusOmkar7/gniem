"""Retry statistical briefing fallbacks using alternative free models only."""
import asyncio
from collections import Counter
from datetime import datetime, UTC
import json
from pathlib import Path

from backend.infrastructure.config.settings import Settings
from backend.infrastructure.services.lookup_service import lookup_service
from scripts.nightly_ai import GroqBriefingClient, is_low_quality_briefing


async def main():
    settings = Settings()
    assert settings.openrouter_api_key, "OpenRouter key is required"
    cache = Path(settings.cache_path)
    processing = cache / "processing_status.json"
    assert json.loads(processing.read_text())["state"] == "complete", "Processing has not finished"
    path = cache / "briefings.json"
    payload = json.loads(path.read_text())
    pending = [code for code, entry in payload.items() if entry["source"] != "openrouter"]
    models = ["nvidia/nemotron-3.5-super-vl:free", "google/gemma-4-31b-it:free"]
    assert all(model.endswith(":free") for model in models)
    codebook = "\n".join(f"{code} -> {lookup_service.get_country_name(code) or code}" for code in payload)
    status = {"state": "running", "pending": len(pending), "retried": 0, "recovered": 0, "attempts": 0}
    report = cache / "briefing_retry_status.json"

    def checkpoint():
        status["sources"] = dict(Counter(entry["source"] for entry in payload.values()))
        temp = report.with_suffix(".json.tmp")
        temp.write_text(json.dumps(status, indent=2))
        temp.replace(report)
        print(json.dumps(status), flush=True)

    checkpoint()
    for code in pending:
        entry = payload[code]
        for model in models:
            client = GroqBriefingClient(api_key=settings.openrouter_api_key, model=model,
                base_url="https://openrouter.ai/api/v1", codebook_context=codebook)
            text = await client.generate_briefing(code, lookup_service.get_country_name(code) or code,
                                                   entry["summary"])
            status["attempts"] += 1
            await asyncio.sleep(3.2)
            if text and not is_low_quality_briefing(text):
                entry.update(briefing=text, source="openrouter", model=model,
                             generated_at=datetime.now(UTC).isoformat())
                status["recovered"] += 1
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(payload, indent=2))
                temporary.replace(path)
                break
        status["retried"] += 1
        checkpoint()
    status["state"] = "complete"
    checkpoint()
    result = json.loads(processing.read_text())
    result["results"]["briefings"]["sources"] = status["sources"]
    result["results"]["briefings"]["free_model_retries"] = status
    result["updated_at"] = datetime.now(UTC).isoformat()
    temporary = processing.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2))
    temporary.replace(processing)


if __name__ == "__main__":
    asyncio.run(main())
