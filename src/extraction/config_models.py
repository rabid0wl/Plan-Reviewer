"""Configuration dataclasses for hybrid extraction requests.

Bundling the provider/API settings into :class:`ExtractionConfig` and the
retry policy into :class:`EscalationConfig` eliminates the 20+ keyword
parameter lists on ``run_hybrid_extraction`` and ``run_batch``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import (
    DEFAULT_ESCALATION_MODEL,
    DEFAULT_EXTRACTION_MODEL,
    ESCALATION_COHERENCE_THRESHOLD,
)

# Provider identifiers — canonical source for the whole extraction package.
PROVIDER_OPENROUTER: str = "openrouter"
PROVIDER_ANTHROPIC: str = "anthropic"


@dataclass(frozen=True)
class ExtractionConfig:
    """API call and provider settings for one extraction request."""

    model: str = DEFAULT_EXTRACTION_MODEL
    api_key: str = ""
    provider: str = PROVIDER_OPENROUTER
    referer: str = ""
    title: str = ""
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_sec: int = 180
    use_structured_output: bool = True
    use_json_schema: bool = True
    use_instructor: bool = True


@dataclass(frozen=True)
class EscalationConfig:
    """Escalation policy — whether and how to retry with a stronger model."""

    enabled: bool = True
    model: str | None = DEFAULT_ESCALATION_MODEL
    coherence_threshold: float = ESCALATION_COHERENCE_THRESHOLD
