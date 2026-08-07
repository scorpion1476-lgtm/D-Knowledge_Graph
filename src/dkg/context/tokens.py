"""Real token counting and cost conversion for the context levers.

Two things this module refuses to fudge.

First, tokens are counted with a real BPE tokenizer (``cl100k_base`` via
tiktoken) whenever it is available, not with a character heuristic. A heuristic
is fine for comparing two texts measured the same way, but it cannot be quoted
as a token count, and a token count is what a cost claim rests on. When tiktoken
is absent the estimator is used and every result says so, so a number is never
presented as tokenizer-measured when it was not.

Second, money is an explicit multiplication the reader can redo. The price table
is configuration, not a measurement: it is stated with the date it was recorded
and can be overridden. Rates change, and a cost figure with an undated rate
buried in it is unfalsifiable. The measurement is the token count; the money is
arithmetic on top of a rate the reader can replace.

tiktoken is a development and benchmarking dependency only. Nothing in the
product runtime imports this module's tokenizer path, so the zero-dependency
core and the air-gap default are unaffected. tiktoken fetches its encoding file
on first use, which is why it belongs to build-and-benchmark tooling and never
to a runtime path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# Fallback estimator, used only when a real tokenizer is unavailable. Documented
# rule: runs of up to four letters, single digits, newlines, and single
# non-alphanumeric characters each count as one; other whitespace is not counted.
_ESTIMATOR_RE = re.compile(r"[A-Za-z]{1,4}|[0-9]|\n|[^\sA-Za-z0-9]")

ENCODING_NAME = "cl100k_base"


@dataclass(frozen=True)
class Rate:
    """Price per million tokens for one model, in US dollars."""

    model: str
    input_per_mtok: float
    output_per_mtok: float


# Price table. THIS IS CONFIGURATION, NOT A MEASUREMENT. It records published
# list rates as read on the date below, in US dollars per million tokens. Rates
# change and vary by tier and provider, so every cost figure derived from this
# table is reported as "at these rates on this date" and the table is printed
# next to the result. Override with a caller-supplied table to price against
# your own contract.
PRICE_TABLE_DATE = "2026-08-05"
PRICE_TABLE: dict[str, Rate] = {
    "frontier": Rate("frontier-class", input_per_mtok=15.00, output_per_mtok=75.00),
    "mid": Rate("mid-class", input_per_mtok=3.00, output_per_mtok=15.00),
    "small": Rate("small-class", input_per_mtok=0.80, output_per_mtok=4.00),
}
DEFAULT_TIER = "mid"


# The pre-staged tokenizer, used in preference to a downloading one. It is the
# vocabulary that ships with the embedding model under models/, so it loads
# local-files-only with no network, which is what makes a real token count
# available in the air-gapped default rather than only on a machine that has
# already fetched an encoding.
_PRESTAGED_TOKENIZER = ("models", "embeddings", "potion-base-8M", "tokenizer.json")


def _prestaged_path():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    candidate = root.joinpath(*_PRESTAGED_TOKENIZER)
    return candidate if candidate.exists() else None


# A word-piece tokenizer refuses any single "word" longer than this and emits
# one unknown token for the whole thing. Left alone that UNDER-COUNTS, badly: a
# 400-character run counts as 1 token instead of 50, so a text full of long
# unbroken strings would look far cheaper than it is. Long runs are split before
# encoding so the tokenizer counts the text instead of giving up on it.
_WORDPIECE_MAX_WORD_CHARS = 100


@lru_cache(maxsize=1)
def _encoder() -> tuple[str, Any] | None:
    """The real tokenizer as (name, encoder), or None when none can be loaded.

    Order matters. tiktoken's BPE is tried FIRST: it is the reference encoding
    published numbers are most comparable against, and it handles long runs
    correctly. It fetches its encoding file on first use, which is why this
    module is build-and-benchmark tooling and never a runtime path.

    The pre-staged word-piece vocabulary is the offline fallback, so a real token
    count is still available with no network. It is a genuinely different
    tokenizer and will not give the same absolute numbers, which is why
    ``tokenizer_name()`` is recorded next to every published figure rather than
    left implicit.

    The estimator is reached only when neither loads, and every result says so.
    """
    try:
        import tiktoken

        return (ENCODING_NAME, tiktoken.get_encoding(ENCODING_NAME))
    except Exception:  # noqa: S110
        # No cached encoding and no network. Fall through to the staged one.
        pass
    path = _prestaged_path()
    if path is not None:
        try:
            from tokenizers import Tokenizer

            tok = Tokenizer.from_file(str(path))
            return (f"prestaged:{path.parent.name}", tok)
        except Exception:  # noqa: S110
            # A staged file that will not load is the same situation as an
            # absent one: fall through rather than failing a measurement.
            pass
    return None


def _split_long_runs(text: str) -> str:
    """Break any run longer than the word-piece limit so it is not lost to UNK."""
    if not any(len(w) > _WORDPIECE_MAX_WORD_CHARS for w in text.split()):
        return text
    out: list[str] = []
    for word in text.split(" "):
        if len(word) <= _WORDPIECE_MAX_WORD_CHARS:
            out.append(word)
            continue
        out.extend(
            word[i : i + _WORDPIECE_MAX_WORD_CHARS]
            for i in range(0, len(word), _WORDPIECE_MAX_WORD_CHARS)
        )
    return " ".join(out)


def tokenizer_available() -> bool:
    return _encoder() is not None


def tokenizer_name() -> str:
    enc = _encoder()
    return enc[0] if enc is not None else "in-repo-estimator"


def count_tokens(text: str) -> int:
    """Token count for one text.

    Uses the real tokenizer when present. Both sides of every comparison in this
    project call this one function, so a comparison is never made between a
    tokenizer count and an estimated count.
    """
    if not text:
        return 0
    enc = _encoder()
    if enc is None:
        return len(_ESTIMATOR_RE.findall(text))
    name, encoder = enc
    if name.startswith("prestaged:"):
        # A word-piece tokenizer adds the model's special tokens to every
        # encode. They are an artefact of the model, not of the text, so they
        # are excluded; otherwise a count of many short texts would be inflated
        # relative to one long one and the comparison would tilt.
        return len(encoder.encode(_split_long_runs(text), add_special_tokens=False).ids)
    return len(encoder.encode(text, disallowed_special=()))


def estimate_tokens(text: str) -> int:
    """The fallback estimator, exposed so its behaviour can be tested directly."""
    return len(_ESTIMATOR_RE.findall(text))


def cost_usd(
    input_tokens: int,
    output_tokens: int = 0,
    *,
    tier: str = DEFAULT_TIER,
    table: dict[str, Rate] | None = None,
) -> float:
    """Dollar cost for a token count at the given tier's rate."""
    rates = table or PRICE_TABLE
    if tier not in rates:
        raise KeyError(f"unknown price tier {tier!r}; known: {sorted(rates)}")
    rate = rates[tier]
    total = (input_tokens / 1_000_000) * rate.input_per_mtok
    total += (output_tokens / 1_000_000) * rate.output_per_mtok
    return round(total, 6)


def measure(text: str, *, tier: str = DEFAULT_TIER) -> dict:
    """Tokens, characters, and cost for one text, in one record."""
    tokens = count_tokens(text)
    return {
        "tokens": tokens,
        "characters": len(text),
        "cost_usd": cost_usd(tokens, tier=tier),
    }


def pricing_note(tier: str = DEFAULT_TIER) -> dict:
    """The provenance a cost figure has to carry to be checkable."""
    rate = PRICE_TABLE[tier]
    return {
        "tier": tier,
        "model_class": rate.model,
        "input_usd_per_mtok": rate.input_per_mtok,
        "output_usd_per_mtok": rate.output_per_mtok,
        "rates_recorded_on": PRICE_TABLE_DATE,
        "note": (
            "Costs are input-token costs at these rates on this date. The price "
            "table is configuration, not a measurement: rates change and vary by "
            "tier and provider. The measurement is the token count; substitute "
            "your own rate and redo the multiplication."
        ),
    }


def tokenizer_note() -> dict:
    return {
        "tokenizer": tokenizer_name(),
        "real_tokenizer": tokenizer_available(),
        "note": (
            "Both sides of every comparison are counted by the same function, so "
            "a ratio never mixes a tokenizer count with an estimate. When the "
            "real tokenizer is absent the fallback estimator is used and this "
            "field says so."
        ),
    }
