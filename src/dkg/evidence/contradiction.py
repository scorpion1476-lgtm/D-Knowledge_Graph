"""Contradiction detection between claims about the same subject.

Two claims contradict when they are about the same subject and attribute but
the object sides are lexically incompatible (numeric mismatch, negation, or
antonym signal). This is a defensible baseline; a neural entailment model can
be plugged in behind the LLM adapter later.

Claims only ever reach the comparator if they are first judged comparable, and
that judgement is where the interesting failure lived. Grouping used to be
exact equality of ``(subject_id, predicate)``. Two documents that disagree
almost never phrase the subject the same way: a runbook writes "the cache TTL
for service 0 is 30 seconds" and an architecture note writes "service 0 uses a
cache TTL of 300 seconds". Those are the same fact stated twice, but they
produce different subject entities and different predicates, so the pair never
landed in one bucket and no disagreement was ever surfaced.

Comparability is therefore the union of two routes:

- ``same-subject-entity``: identical resolved subject entity and identical
  predicate. This is the original rule, kept unchanged, so nothing that used to
  be found stops being found.
- ``paraphrase``: the two claims share the same subject identifiers and one
  claim's topic is wholly contained in the other's.

A claim's *topic* is the set of normalised content words of its subject plus
those of its object, with numbers in the object left out. The asymmetry is
deliberate and is the whole point: a number in the object is the value under
dispute, which is exactly what the comparator is about to examine, while a
number in the subject identifies what the claim is about. "Service 0" and
"service 1" are different subjects and must never be compared; "30 seconds" and
"300 seconds" are the disagreement.

Subject identifiers (the digit-bearing tokens of the subject) must match
exactly. Without that guard, "the cache TTL for service 0" and "service 1" have
enough words in common to look like paraphrases of one another, and the
detector would invent a disagreement between two services that simply hold
different values.

The result is advisory and over-approximate. Containment is a lexical test, not
an entailment model: it can group two claims that a reader would not, and it
misses paraphrases that share no vocabulary. Every signal carries the route and
the reason that produced it so a human can dismiss it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.db import Database

_NEGATION = re.compile(r"\b(no|not|never|none|without)\b", re.IGNORECASE)
_NUM = re.compile(r"[-+]?\d*\.?\d+")
# A number and whatever word follows it, so a value can be read with its unit.
_NUM_UNIT = re.compile(r"([-+]?\d*\.?\d+)\s*([a-z]+)?", re.IGNORECASE)

# Units by dimension, with the factor to that dimension's base. Two values in
# the same dimension are compared after conversion, so "5 seconds" and "5000
# milliseconds" are recognised as the same quantity rather than as a numeric
# disagreement of 5 against 5000. Two values in *different* dimensions are not
# comparable at all and produce no numeric signal.
#
# Deliberately small, and deliberately free of ambiguous abbreviations: "m"
# could be metres or minutes and "b" could be bits or bytes, so neither is
# listed. An unrecognised word is not a unit, and the values are then compared
# as bare numbers exactly as before, which is what keeps "3 nodes" against
# "5 replica nodes" working.
_UNITS: dict[str, tuple[str, float]] = {
    "ms": ("time", 0.001), "millisecond": ("time", 0.001), "milliseconds": ("time", 0.001),
    "sec": ("time", 1.0), "secs": ("time", 1.0), "second": ("time", 1.0), "seconds": ("time", 1.0),
    "min": ("time", 60.0), "mins": ("time", 60.0), "minute": ("time", 60.0), "minutes": ("time", 60.0),
    "hr": ("time", 3600.0), "hrs": ("time", 3600.0), "hour": ("time", 3600.0), "hours": ("time", 3600.0),
    "day": ("time", 86400.0), "days": ("time", 86400.0),
    "week": ("time", 604800.0), "weeks": ("time", 604800.0),
    "byte": ("size", 1.0), "bytes": ("size", 1.0),
    "kb": ("size", 1e3), "kilobyte": ("size", 1e3), "kilobytes": ("size", 1e3),
    "mb": ("size", 1e6), "megabyte": ("size", 1e6), "megabytes": ("size", 1e6),
    "gb": ("size", 1e9), "gigabyte": ("size", 1e9), "gigabytes": ("size", 1e9),
    "tb": ("size", 1e12), "terabyte": ("size", 1e12), "terabytes": ("size", 1e12),
    "percent": ("ratio", 1.0), "pc": ("ratio", 1.0),
}


def _first_quantity(text: str) -> tuple[float, str | None] | None:
    """The first number in a phrase, with its dimension if it carries a unit."""
    m = _NUM_UNIT.search(text)
    if not m:
        return None
    value = float(m.group(1))
    word = (m.group(2) or "").lower()
    known = _UNITS.get(word)
    if known is None:
        return (value, None)
    dimension, factor = known
    return (value * factor, dimension)

_ANTONYMS = {
    ("true", "false"),
    ("yes", "no"),
    ("open", "closed"),
    ("supported", "unsupported"),
    ("safe", "unsafe"),
    ("enabled", "disabled"),
    ("verified", "unverified"),
    ("public", "private"),
}

# Tokens: words, and identifier-like runs such as ``layer_0_gateway`` or
# ``v1.2`` kept whole so an identifier is not shattered into digits.
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_DIGIT = re.compile(r"\d")

# Function words carry no aboutness. Deliberately small and domain-neutral: a
# longer list would start encoding this corpus's vocabulary into the matcher.
_STOPWORDS = frozenset(
    """
    a an the of for in on at to from by with and or but as that this these those
    it its is are was were be been being has have had will would can could
    should may might must do does did if then than so such which who whom whose
    when where while into over under per via about after before during between
    each any all some more most other there their his her they them we you your
    our i he she
    """.split()
)

# Bound on the pairs the paraphrase route will examine in one scan. The rarest
# topic token indexes candidates, so this is generous in practice; it exists so
# a pathological graph cannot make a scan unbounded, and it is reported rather
# than applied silently.
MAX_PAIR_COMPARISONS = 2_000_000

# And a bound on the answer, not only on the work. One subject asserted many
# times over produces a quadratic number of signals from a linear number of
# comparisons, so a comparison budget alone does not bound the report. Whichever
# cap bites sets the truncated flag.
MAX_SIGNALS = 10_000


@dataclass
class ContradictionSignal:
    score: float
    reason: str


@dataclass(frozen=True)
class ContradictionReport:
    """Every signal found, plus whether the scan ran to completion."""

    signals: list[dict] = field(default_factory=list)
    comparisons: int = 0
    claims_scanned: int = 0
    truncated: bool = False


def compare_claims(a_obj: str, b_obj: str) -> ContradictionSignal:
    a, b = (a_obj or "").strip().lower(), (b_obj or "").strip().lower()
    if not a or not b:
        return ContradictionSignal(0.0, "one side empty")
    if a == b:
        return ContradictionSignal(0.0, "identical text")

    # numeric comparison, unit-aware where BOTH sides carry a recognised unit
    qa, qb = _first_quantity(a), _first_quantity(b)
    if qa is not None and qb is not None:
        (a_val, a_dim), (b_val, b_dim) = qa, qb
        raw_a, raw_b = _NUM.search(a), _NUM.search(b)
        both_united = a_dim is not None and b_dim is not None
        if both_united and a_dim != b_dim:
            # Different dimensions. Two quantities that are not of the same kind
            # cannot disagree numerically, so say nothing rather than compare
            # the bare numbers and invent a conflict.
            pass
        elif both_united:
            if a_val != b_val:
                return ContradictionSignal(
                    0.7,
                    f"different {a_dim} values: {raw_a.group(0) if raw_a else a_val} vs "
                    f"{raw_b.group(0) if raw_b else b_val} ({a_val} vs {b_val} in base units)",
                )
            # Equal once converted. "5 seconds" and "5000 milliseconds" are the
            # same quantity written two ways, and calling that a contradiction is
            # worse than saying nothing. Fall through rather than return: the
            # numbers agreeing says nothing about whether the two sentences
            # around them do, and returning here made "enabled for 5 seconds"
            # against "disabled for 5000 milliseconds" score zero.
        else:
            # At most one side carries a unit, so there is no conversion to do.
            # Compare the numbers exactly as written. Converting the unitful side
            # and comparing it against the other side's bare number invented a
            # conflict between "5 minutes" and "5 nodes" (300 against 5) and lost
            # a real one between "2 hours" and "7200 requests".
            a_raw_val = float(raw_a.group(0)) if raw_a else a_val
            b_raw_val = float(raw_b.group(0)) if raw_b else b_val
            if a_raw_val != b_raw_val:
                return ContradictionSignal(
                    0.7, f"different numeric values: {a_raw_val} vs {b_raw_val}"
                )

    # negation flip
    a_neg = bool(_NEGATION.search(a))
    b_neg = bool(_NEGATION.search(b))
    if a_neg != b_neg:
        return ContradictionSignal(0.5, "negation asymmetry")

    # antonym pair
    for x, y in _ANTONYMS:
        if (x in a and y in b) or (y in a and x in b):
            return ContradictionSignal(0.8, f"antonym pair: {x!r} vs {y!r}")

    return ContradictionSignal(0.0, "no contradiction signal")


def _fold_plural(token: str) -> str:
    """Light, language-agnostic plural fold so "seconds" meets "second".

    Deliberately not a stemmer. It only removes a regular trailing plural, and
    leaves short tokens and double-s endings ("class", "ttl") alone.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _content_tokens(text: str, *, drop_numeric: bool) -> frozenset[str]:
    """Normalised content words of a phrase."""
    out: set[str] = set()
    for raw in _TOKEN.findall((text or "").lower()):
        if raw in _STOPWORDS:
            continue
        if drop_numeric and _DIGIT.search(raw):
            continue
        out.add(_fold_plural(raw))
    return frozenset(out)


def _identifiers(subject_text: str) -> frozenset[str]:
    """Digit-bearing tokens of a subject: what the claim is about, not what it
    asserts. Two claims whose subject identifiers differ are about different
    things however similar their wording."""
    return frozenset(
        _fold_plural(raw)
        for raw in _TOKEN.findall((subject_text or "").lower())
        if _DIGIT.search(raw)
    )


def _topic(subject_text: str, object_text: str) -> frozenset[str]:
    return _content_tokens(subject_text, drop_numeric=False) | _content_tokens(
        object_text, drop_numeric=True
    )


def _sort_key(claim: dict) -> tuple[str, str, str, str]:
    """A content-derived order for one claim.

    Deliberately not the claim id. Claim ids descend from the chunk id, which
    descends from the source URI, which for anything ingested out of a
    temporary directory contains a path that differs on every run. Ordering on
    the id therefore made the *content* of a report vary between runs of the
    same scan over the same corpus: the same six signals came back, but which
    side was left and which was right flipped, so a published artifact was not
    byte-reproducible. Everything below is derived from the claim text.
    """
    return (
        str(claim.get("subject_text") or ""),
        str(claim.get("predicate") or ""),
        str(claim.get("object_text") or ""),
        str(claim.get("claim_id") or ""),
    )


def _pair_key(a: dict, b: dict) -> tuple[str, str]:
    left, right = str(a["claim_id"]), str(b["claim_id"])
    return (left, right) if left <= right else (right, left)


def _ordered(a: dict, b: dict) -> tuple[dict, dict]:
    return (a, b) if _sort_key(a) <= _sort_key(b) else (b, a)


def _same_subject_entity_pairs(
    claims: list[dict], budget: int
) -> tuple[list[tuple[int, int]], int, bool]:
    """The original rule: identical subject entity and identical predicate.

    Bounded by the same budget as the paraphrase route. This is a nested loop
    over a bucket, so one subject asserted a few thousand times is quadratic;
    the budget is what stops a pathological graph turning a scan into a hang.
    """
    buckets: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(claims):
        buckets.setdefault((str(c["subject_id"]), str(c["predicate"])), []).append(i)
    pairs: list[tuple[int, int]] = []
    comparisons = 0
    for _key in sorted(buckets):
        group = buckets[_key]
        for x in range(len(group)):
            for y in range(x + 1, len(group)):
                if comparisons >= budget:
                    return pairs, comparisons, True
                comparisons += 1
                pairs.append((group[x], group[y]))
    return pairs, comparisons, False


#: How many tokens of the smaller topic may be missing from the larger and the
#: two still count as the same topic.
#:
#: ZERO, and the history of this constant is worth keeping because it is the
#: whole argument. Strict containment misses three real disagreements in the
#: held-out corpus where one differing word separates two phrasings of the same
#: fact ("replica count" against "replicated", "pool size" against "pool of").
#: Raising this to 1 catches all three and took recall from 6 of 9 to 9 of 9.
#:
#: It was then reverted, because an adversarial review showed what it costs. The
#: corpus had no negative case in which two DIFFERENT subjects are distinguished
#: by a word rather than a numeral: its one different-subjects negative separates
#: "queue 11" from "queue 12", which the subject-identifier gate rejects before
#: topic matching is reached. With slack of 1, "the cache size for the gateway is
#: 100 megabytes" and "the cache size for the router is 250 megabytes" are
#: reported as a contradiction. So are upload against download, and the retry
#: budget for the api against the one for the worker.
#:
#: Those three cases are now IN the corpus (N7, N8, N9). Measured with them
#: present: slack 0 gives recall 0.6667 and precision 0.75; slack 1 gives recall
#: 1.0 and precision 0.5294. The recall was real and so was the cost, and the
#: cost is worse: a scanner whose output is advisory earns its place by being
#: worth reading, and one in every two signals being wrong is not.
#:
#: The deeper reason it cannot simply be tuned: P9, one of the three misses, is
#: "the connection pool size" against "a pool of 50 connections in the payment
#: service". Lexically that is the SAME SHAPE as gateway against router. No
#: threshold separates them, because the difference is meaning, not vocabulary.
#: Catching P9 needs entailment, which this is not.
_TOPIC_SLACK = 0

#: Below this, the shared vocabulary is too thin for the slack above to be
#: anything but noise. Two topics sharing one token and differing by one are not
#: a paraphrase, they are two short sentences about different things.
_MIN_TOPIC_OVERLAP = 2


def _topics_pair(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether two topics are close enough to be about the same thing.

    Containment, which is the rule. The slack branch below is retained and set
    to zero rather than deleted, because the constant above records a measured
    result about this exact predicate and a reader should be able to see the
    thing the measurement is about.
    """
    if a <= b or b <= a:
        return True
    if _TOPIC_SLACK <= 0:
        return False
    shared = a & b
    if len(shared) < _MIN_TOPIC_OVERLAP:
        return False
    smaller = a if len(a) <= len(b) else b
    return len(smaller - shared) <= _TOPIC_SLACK


def _paraphrase_pairs(claims: list[dict], budget: int) -> tuple[list[tuple[int, int]], int, bool]:
    """Pairs whose subjects agree on identifiers and whose topics nest.

    Candidates come from an inverted index on the rarest topic token of each
    claim. Seeding is done from both ends of every candidate pair, because the
    guarantee only runs one way: if topic A is contained in topic B then every
    token of A is in B, so seeding from A always reaches B, but B's own rarest
    token may be one of the extra tokens A does not have. Scanning from each
    claim in turn and de-duplicating the pairs closes that hole.
    """
    by_ident: dict[frozenset[str], list[int]] = {}
    for i, c in enumerate(claims):
        by_ident.setdefault(c["_identifiers"], []).append(i)

    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    comparisons = 0
    for group in by_ident.values():
        if len(group) < 2:
            continue
        postings: dict[str, list[int]] = {}
        for i in group:
            for token in claims[i]["_topic"]:
                postings.setdefault(token, []).append(i)
        for i in group:
            topic = claims[i]["_topic"]
            if not topic:
                continue
            # Seed from the RAREST FEW tokens, not the single rarest.
            #
            # One seed is sufficient only under strict containment: if every
            # token of A is in B then whichever token of A we seed from reaches
            # B. Once a topic may differ by up to _TOPIC_SLACK tokens that
            # guarantee is gone, and the rarest token is exactly the one most
            # likely to be the unshared one, so the pair is never even
            # considered. Taking _TOPIC_SLACK + 1 of the rarest restores the
            # guarantee from the smaller side: at most _TOPIC_SLACK of them can
            # be unshared, so at least one must hit the other claim's postings.
            ordered = sorted(topic, key=lambda t: (len(postings.get(t, ())), t))
            candidates: list[int] = []
            for seed in ordered[: _TOPIC_SLACK + 1]:
                candidates.extend(postings.get(seed, ()))
            for j in candidates:
                if j == i:
                    continue
                key = (i, j) if i < j else (j, i)
                if key in seen:
                    continue
                seen.add(key)
                if comparisons >= budget:
                    return pairs, comparisons, True
                comparisons += 1
                other = claims[j]["_topic"]
                if _topics_pair(topic, other):
                    pairs.append(key)
    return pairs, comparisons, False


def scan_contradictions(
    db: Database,
    *,
    tenant_id: str = "local",
    threshold: float = 0.5,
    max_comparisons: int = MAX_PAIR_COMPARISONS,
    max_signals: int = MAX_SIGNALS,
) -> ContradictionReport:
    """Scan every claim pair judged comparable and report the signals found.

    Bounded on both dimensions: how many pairs are examined, and how many
    signals come back. A cap on one is not a bound, because a graph with one
    heavily repeated subject produces a quadratic number of signals from a
    linear number of comparisons. Either cap sets ``truncated``.
    """
    rows = db.fetchall(
        """
        SELECT c.claim_id   AS claim_id,
               c.subject_id AS subject_id,
               c.predicate  AS predicate,
               c.object_text AS object_text,
               c.chunk_id   AS chunk_id,
               e.canonical  AS subject_text
        FROM claims c
        JOIN entities e ON e.entity_id = c.subject_id
        WHERE c.tenant_id = ? AND c.subject_id IS NOT NULL
        ORDER BY c.claim_id;
        """,
        (tenant_id,),
    )
    claims: list[dict] = []
    for r in rows:
        d = dict(r)
        subject_text = str(d.get("subject_text") or "")
        d["_identifiers"] = _identifiers(subject_text)
        d["_topic"] = _topic(subject_text, str(d.get("object_text") or ""))
        claims.append(d)

    # Both routes draw on one budget, and both report against it. Bounding only
    # the paraphrase route would have given the report the appearance of a bound
    # the scan did not have.
    routes: dict[tuple[str, str], str] = {}
    ordered_pairs: list[tuple[int, int]] = []
    exact, exact_comparisons, exact_truncated = _same_subject_entity_pairs(claims, max_comparisons)
    for i, j in exact:
        key = _pair_key(claims[i], claims[j])
        if key not in routes:
            routes[key] = "same-subject-entity"
            ordered_pairs.append((i, j))
    remaining = max(0, max_comparisons - exact_comparisons)
    para, para_comparisons, para_truncated = _paraphrase_pairs(claims, remaining)
    for i, j in para:
        key = _pair_key(claims[i], claims[j])
        if key not in routes:
            routes[key] = "paraphrase"
            ordered_pairs.append((i, j))
    comparisons = exact_comparisons + para_comparisons
    truncated = exact_truncated or para_truncated

    out: list[dict] = []
    for i, j in ordered_pairs:
        left, right = _ordered(claims[i], claims[j])
        sig = compare_claims(left["object_text"], right["object_text"])
        if sig.score < threshold:
            continue
        route = routes[_pair_key(left, right)]
        out.append(
            {
                "left": {k: v for k, v in left.items() if not k.startswith("_")},
                "right": {k: v for k, v in right.items() if not k.startswith("_")},
                "score": sig.score,
                "reason": sig.reason,
                "route": route,
            }
        )
    # Content-derived order, so the same corpus produces the same report even
    # though claim ids carry the ingest path.
    out.sort(key=lambda s: (_sort_key(s["left"]), _sort_key(s["right"])))
    if len(out) > max_signals:
        out = out[:max_signals]
        truncated = True
    return ContradictionReport(
        signals=out,
        comparisons=comparisons,
        claims_scanned=len(claims),
        truncated=truncated,
    )


def find_contradictions(db: Database, *, tenant_id: str = "local") -> list[dict]:
    """Contradiction signals in the graph, deterministically ordered.

    Callers that need to know whether the scan hit a bound must use
    ``scan_contradictions``, which returns the flag. This wrapper exists for
    callers that only want the list.
    """
    return scan_contradictions(db, tenant_id=tenant_id).signals
