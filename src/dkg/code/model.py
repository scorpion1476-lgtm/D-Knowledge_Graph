"""Code plane node and edge model, and the documented confidence heuristic.

Code entities are stored in the shared ``entities`` table with a ``code:<kind>``
kind, and edges in the shared ``relationships`` table with a ``code:<predicate>``
predicate and the confidence in ``weight``. No parallel store.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NODE_KINDS = ("module", "class", "function", "method", "type", "test", "route", "config", "entrypoint")

# The structural vocabulary: what a parser can see in any language.
STRUCTURAL_PREDICATES = ("defines", "calls", "imports", "inherits", "tested_by")

# Framework relations are NOT calls or imports, and flattening them into those
# loses the thing that makes them useful. A route does not "call" its handler in
# any sense the parser saw; a framework does, at runtime, based on a URL. A
# controller does not "import" a template; it renders one. Recording each as
# what it is means a question like "what serves this endpoint" can be answered
# by following one predicate instead of guessing which imports are really
# renders.
#
# Every one of these is still structural and over-approximate in exactly the way
# the rest of the plane is: a route registered through a variable, or a template
# named at runtime, is not seen at all.
FRAMEWORK_PREDICATES = (
    # An HTTP route or console command reaches the code that handles it.
    "routes_to",
    # Code renders a template or view.
    "renders",
    # A model declares a relation to another model (an ORM association).
    "relates_to",
    # A configuration key is read by the code that consumes it.
    "configures",
    # A scheduled or event-driven invocation reaches its handler.
    "dispatches",
)

EDGE_PREDICATES = STRUCTURAL_PREDICATES + FRAMEWORK_PREDICATES

# What each framework predicate means, carried into the product so a consumer
# reading an edge kind does not have to guess.
PREDICATE_EXPLANATIONS = {
    "defines": "the parser saw this definition inside that one",
    "calls": "this symbol references that one as a call",
    "imports": "this module brings that one into scope",
    "inherits": "this type derives from that one",
    "tested_by": "that test exercises this symbol",
    "routes_to": "a request or command matching this route reaches that handler",
    "renders": "this code renders that template or view",
    "relates_to": "this model declares an association with that model",
    "configures": "this configuration key is read by that code",
    "dispatches": "this scheduled or event-driven trigger invokes that handler",
}

# Confidence heuristic, deterministic and documented (not arbitrary numbers):
# - a structural containment edge (defines) is certain by construction.
# - a reference that resolves to exactly one in-repo definition scores high.
# - a name that matches a definition but not uniquely is a name-based guess.
# - a name with no in-repo definition (external or unknown) scores low.
CONF_DEFINES = 1.0
# A type-aware, language-server-resolved edge is stronger evidence than a unique
# name match, which is stronger than an ambiguous name match.
CONF_TYPE_RESOLVED = 0.95
CONF_RESOLVED = 0.9
CONF_NAME_MATCH = 0.6
CONF_UNRESOLVED = 0.3
CONF_INHERIT_RESOLVED = 0.9
CONF_INHERIT_NAME = 0.6
CONF_TESTED_BY = 0.5
# A symbol found by the documented pattern fallback rather than by a grammar is
# weaker evidence than a parsed one, so every edge from a fallback-parsed file is
# scaled down. The factor is a single documented constant rather than a per-edge
# guess, and it never raises a confidence: 0.9 resolved becomes 0.63, which stays
# below the 0.6 name-match band's parsed equivalent only when it was already
# ambiguous. Fidelity is also recorded on the node, so a consumer can filter
# rather than having to infer it from a weight.
FALLBACK_CONFIDENCE_FACTOR = 0.7

# How a file was read. Recorded on every code entity so a fallback-derived
# symbol is never mistaken for a parsed one downstream.
FIDELITY_GRAMMAR = "grammar"
FIDELITY_FALLBACK = "fallback"

# -- Three-tier edge confidence -----------------------------------------------
#
# The numeric weight above is precise but not self-explaining: a consumer
# reading 0.6 has to know the heuristic to know what it means. The tier is the
# same fact in a form a reader can act on, and the two are always reported
# together so the tier never replaces the number.
#
#   extracted  The edge was READ from the source. Containment is extracted by
#              construction: the parser saw the definition inside its parent.
#              Nothing was guessed.
#   inferred   The edge was RESOLVED to exactly one definition, by a language
#              server, by local dataflow, or by a name that matched a single
#              in-repo definition. A judgement was made, and it had one answer.
#   ambiguous  The name matched several definitions, or none. The edge is a
#              candidate, not a fact, and an answer resting on it is
#              over-approximate.
#
# The boundaries are derived from the constants above rather than chosen
# separately, so changing a constant cannot silently move an edge between tiers
# without moving the boundary with it.
TIER_EXTRACTED = "extracted"
TIER_INFERRED = "inferred"
TIER_AMBIGUOUS = "ambiguous"
CONFIDENCE_TIERS = (TIER_EXTRACTED, TIER_INFERRED, TIER_AMBIGUOUS)

# An edge at or above this is extracted; at or above the next it is inferred;
# below that it is ambiguous.
TIER_EXTRACTED_MIN = CONF_DEFINES
TIER_INFERRED_MIN = CONF_INHERIT_NAME + 0.01

TIER_EXPLANATIONS = {
    TIER_EXTRACTED: (
        "read directly from the source; containment the parser observed, not a guess"
    ),
    TIER_INFERRED: (
        "resolved to exactly one in-repo definition, by a language server, local "
        "dataflow, or a uniquely matching name"
    ),
    TIER_AMBIGUOUS: (
        "the name matched several definitions or none, so this edge is a candidate "
        "rather than a fact and any answer resting on it over-approximates"
    ),
}


def confidence_tier(weight: float | None) -> str:
    """The tier a numeric edge confidence falls in.

    A missing weight is treated as ambiguous rather than as certain: an edge
    that lost its confidence is exactly the edge that should not be trusted.
    """
    if weight is None:
        return TIER_AMBIGUOUS
    value = float(weight)
    if value >= TIER_EXTRACTED_MIN:
        return TIER_EXTRACTED
    if value >= TIER_INFERRED_MIN:
        return TIER_INFERRED
    return TIER_AMBIGUOUS


def confidence_record(weight: float | None) -> dict:
    """Tier, number, and the reason, together. Never the tier on its own."""
    tier = confidence_tier(weight)
    return {
        "tier": tier,
        "confidence": round(float(weight), 4) if weight is not None else None,
        "why": TIER_EXPLANATIONS[tier],
    }


def entity_kind(node_kind: str) -> str:
    return f"code:{node_kind}"


def edge_predicate(predicate: str) -> str:
    return f"code:{predicate}"


@dataclass
class Symbol:
    kind: str  # one of NODE_KINDS
    name: str  # short name
    qualified: str  # stable id, for example path/to/file.py::Class.method
    start_line: int
    end_line: int
    text: str
    parent: str | None = None  # qualified id of the enclosing symbol


@dataclass
class Reference:
    from_qualified: str  # the symbol making the reference
    kind: str  # calls | imports | inherits
    name: str  # referenced short name (unresolved at parse time)


@dataclass
class ParsedFile:
    path: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    # How this file was read: a real grammar parse, or the documented pattern
    # fallback. Carried through to the graph so the distinction survives.
    fidelity: str = FIDELITY_GRAMMAR
