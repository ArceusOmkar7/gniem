"""LLM analysis service using OpenRouter or Groq chat completions.

Uses Groq-hosted models to perform deep intelligence analysis on news article text.
"""

from __future__ import annotations

import structlog
import httpx

try:
    from groq import AsyncGroq
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    AsyncGroq = None  # type: ignore[assignment]

from backend.domain.models.event import EventAnalysis
from backend.domain.ports.ports import ILLMAnalysisService
from backend.infrastructure.config.settings import Settings

logger = structlog.get_logger(__name__)


class LLMAnalysisService(ILLMAnalysisService):
    """Groq implementation of the LLM analysis service."""

    def __init__(self, settings: Settings) -> None:
        if AsyncGroq is None and not settings.openrouter_api_key:
            raise RuntimeError("groq is required for LLM analysis but is not installed")
        if settings.openrouter_api_key:
            self._client = None
            self._model_name = settings.openrouter_model_name
        elif settings.groq_api_key:
            self._client = AsyncGroq(api_key=settings.groq_api_key)
            self._model_name = settings.groq_model_name
        else:
            raise RuntimeError("An OpenRouter or Groq API key is required for LLM analysis")
        self._openrouter_api_key = settings.openrouter_api_key
        self._fallback_model_name = settings.openrouter_fallback_model_name

    async def analyze_event(self, article_text: str) -> EventAnalysis:
        """Analyze article text and return structured intelligence."""
        logger.info("llm_analysis_start", text_length=len(article_text))

        try:
            payload = dict(
                model=self._model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a geopolitical intelligence analyst specializing in GDELT event data. "
                            "Analyze news articles and return a JSON object with exactly these keys:\n\n"
                            "- summary: 2–3 sentence factual summary of the core event, actors, and location. No speculation.\n"
                            "- sentiment: Overall tone of the article. Must be exactly one of: 'Positive', 'Neutral', 'Negative'.\n"
                            "- entities: Object with three arrays — 'countries' (ISO country names), 'organizations' (named orgs, alliances, bodies), 'persons' (named individuals). Each array may be empty.\n"
                            "- themes: Array of 3–6 short thematic tags relevant to this event (e.g. 'armed conflict', 'sanctions', 'diplomatic talks', 'humanitarian crisis'). Use lowercase, specific terms — not generic words like 'news' or 'world'.\n"
                            "- confidence: Float from 0.0 to 1.0 reflecting how much of the article was usable for analysis. "
                            "Use 0.9+ for full, clear articles. Use 0.4–0.6 for fragments, paywalled, or mostly-boilerplate text. Use below 0.4 if the article is largely unintelligible or irrelevant.\n\n"
                            "Return only the JSON object. No markdown, no explanation, no preamble."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Article Text:\n---\n{article_text}\n---\n\n"
                            "Analyze the article above and return the JSON intelligence report."
                        ),
                    },
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            if self._openrouter_api_key:
                payload["max_tokens"] = 1024
                payload["reasoning"] = {"enabled": False}
                payload["models"] = list(dict.fromkeys([payload.pop("model"), self._fallback_model_name]))
                async with httpx.AsyncClient(timeout=90) as client:
                    response = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self._openrouter_api_key}"},
                        json=payload,
                    )
                    response.raise_for_status()
                    choices = response.json().get("choices", [])
                    content = choices[0]["message"].get("content") if choices else None
            else:
                completion = await self._client.chat.completions.create(**payload)
                content = completion.choices[0].message.content if completion.choices else None
            if not content:
                raise RuntimeError("Groq returned an empty completion")

            analysis = EventAnalysis.model_validate_json(content)
            
            logger.info("llm_analysis_success", sentiment=analysis.sentiment)
            return analysis
            
        except Exception as exc:
            logger.error("llm_analysis_failed", error=str(exc))
            raise
