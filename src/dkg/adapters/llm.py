"""LLM adapter interface.

D-Knowledge_Graph does not bundle any model provider. Callers that want LLM
assistance implement :class:`LLMAdapter` and register it on the capability
registry. The core platform always has a deterministic fallback and never
requires an LLM adapter to be present.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenerationRequest:
    system: str
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.0
    stop: list[str] | None = None


@dataclass
class GenerationResponse:
    text: str
    used_tokens: int
    finish_reason: str = "stop"
    metadata: dict | None = None


class LLMAdapter(ABC):
    name: str

    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResponse: ...

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...


class NullLLMAdapter(LLMAdapter):
    """Deterministic no-op adapter used when no real LLM is registered."""

    name = "null"

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            text="",
            used_tokens=0,
            finish_reason="stop",
            metadata={"adapter": "null", "notice": "no LLM adapter registered"},
        )

    def available(self) -> tuple[bool, str]:
        return False, "no LLM adapter registered; deterministic fallback in use"


class DeterministicLLMAdapter(LLMAdapter):
    """Offline, deterministic bundled adapter.

    Not an LLM in the neural sense; it is a template-based responder
    that always produces a bounded, reproducible answer derived from
    the prompt. This keeps the LLM adapter interface exercised with a
    real, tested implementation so downstream code does not need to
    check for ``None`` in the offline default path.

    The response is a short summary of the prompt with a stable prefix
    so callers can detect the deterministic backend easily.
    """

    name = "deterministic"

    def generate(self, req: GenerationRequest) -> GenerationResponse:
        prompt = (req.prompt or "").strip()
        if not prompt:
            text = "[deterministic-llm] empty prompt"
        else:
            # Take the first sentence and cap length, no randomness.
            first = prompt.split(".", 1)[0].strip()
            first = first[: min(len(first), req.max_tokens * 4)]
            text = f"[deterministic-llm] summary: {first}"
        stop_reason = "stop"
        for s in req.stop or []:
            if s and s in text:
                text = text.split(s, 1)[0]
                stop_reason = "stop_sequence"
                break
        return GenerationResponse(
            text=text,
            used_tokens=min(req.max_tokens, max(1, len(text.split()))),
            finish_reason=stop_reason,
            metadata={
                "adapter": "deterministic",
                "notice": "offline bundled default, no external model",
            },
        )

    def available(self) -> tuple[bool, str]:
        return True, "built-in deterministic responder, no external service"
