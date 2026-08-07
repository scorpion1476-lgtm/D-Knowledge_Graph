#!/usr/bin/env python3
"""Generate the retained community-detection corpus.

Two sub-corpora with ground truth known by construction:

structural: a ring of K cliques joined by single bridge edges. The ground-truth
communities are the cliques. It is purely structural, so both the default
detector (Mnemosyne) and the refinement detector (Ariadne) recover it by optimizing
modularity; the benchmark expects them to tie.

semantic: T topics of E entities each, laid out on a T-by-E torus so that
structure alone cannot prefer topics. Every entity carries a real topical term as
its display name. Each entity has exactly two within-topic edges (its topic forms
a cycle over the E entities) and two cross-topic edges (each position forms a
cycle over the T topics). Every node therefore has degree four and the two
groupings (by topic and by position) are structurally symmetric, so unweighted
structural modularity cannot single out the topics. Ariadne re-weights edges by
the embedding similarity of the connected names, so the within-topic edges (close
names) dominate the cross-topic ones (distant names) and the topics emerge. The
ground truth is the topic of each entity.

Run ``python tests/graph/corpus/generate_corpus.py`` to regenerate
``graph_corpus.json`` in place. The corpus is retained and versioned.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

K_CLIQUES = 16
CLIQUE_SIZE = 5

# Five clearly distinct semantic domains, eight real terms each. Same-domain
# terms are close in the embedding space; cross-domain terms are not.
TOPIC_TERMS: dict[str, list[str]] = {
    "databases": [
        "database index", "query optimizer", "table schema", "sql join",
        "transaction log", "foreign key", "btree index", "query planner",
    ],
    "astronomy": [
        "red giant star", "white dwarf", "black hole", "spiral galaxy",
        "solar flare", "neutron star", "cosmic dust", "orbital period",
    ],
    "cooking": [
        "sourdough bread", "roast chicken", "olive oil", "simmered sauce",
        "chopped onion", "baking flour", "fresh basil", "grilled steak",
    ],
    "music": [
        "electric guitar", "piano chord", "drum rhythm", "violin bow",
        "jazz melody", "bass line", "vocal harmony", "musical tempo",
    ],
    "medicine": [
        "blood pressure", "immune system", "antibiotic dose", "heart rate",
        "vaccine booster", "nerve cell", "bone fracture", "insulin level",
    ],
}


def _structural() -> dict:
    nodes = [f"c{c}_{i}" for c in range(K_CLIQUES) for i in range(CLIQUE_SIZE)]
    edges: list[list] = []
    for c in range(K_CLIQUES):
        members = [f"c{c}_{i}" for i in range(CLIQUE_SIZE)]
        for a in range(CLIQUE_SIZE):
            for b in range(a + 1, CLIQUE_SIZE):
                edges.append([members[a], members[b], 1.0])
    # Ring of single bridges between clique 0-nodes.
    for c in range(K_CLIQUES):
        edges.append([f"c{c}_0", f"c{(c + 1) % K_CLIQUES}_0", 1.0])
    ground_truth = {f"c{c}_{i}": c for c in range(K_CLIQUES) for i in range(CLIQUE_SIZE)}
    return {
        "note": f"Ring of {K_CLIQUES} cliques (size {CLIQUE_SIZE}) joined by single bridge edges; ground truth is the {K_CLIQUES} cliques. Purely structural.",
        "cliques": K_CLIQUES,
        "clique_size": CLIQUE_SIZE,
        "nodes": nodes,
        "edges": edges,
        "ground_truth": ground_truth,
    }


def _semantic() -> dict:
    topics = list(TOPIC_TERMS)
    n_topics = len(topics)
    n_ent = len(next(iter(TOPIC_TERMS.values())))
    entities: dict[str, str] = {}
    ground_truth: dict[str, int] = {}
    for ti, topic in enumerate(topics):
        for si, term in enumerate(TOPIC_TERMS[topic]):
            eid = f"t{ti}_{si}"
            entities[eid] = term
            ground_truth[eid] = ti
    edges: list[list] = []
    seen: set[tuple[str, str]] = set()

    def add(u: str, v: str) -> None:
        key = (u, v) if u < v else (v, u)
        if u != v and key not in seen:
            seen.add(key)
            edges.append([key[0], key[1], 1.0])

    # Within-topic cycle over the E entities of each topic (2 edges per node).
    for ti in range(n_topics):
        for si in range(n_ent):
            add(f"t{ti}_{si}", f"t{ti}_{(si + 1) % n_ent}")
    # Cross-topic cycle over the T topics at each position (2 edges per node).
    for si in range(n_ent):
        for ti in range(n_topics):
            add(f"t{ti}_{si}", f"t{(ti + 1) % n_topics}_{si}")
    return {
        "note": (
            "T topics of E entities on a T-by-E torus. Each node has two "
            "within-topic and two cross-topic edges, so structure is symmetric "
            "between the topic and position groupings and unweighted modularity "
            "cannot single out topics; semantic edge weighting can. Ground truth "
            "is the topic per entity."
        ),
        "topics": n_topics,
        "entities_per_topic": n_ent,
        "entities": entities,
        "edges": edges,
        "ground_truth": ground_truth,
    }


def main() -> int:
    corpus = {
        "note": "Retained graph corpus for community-detection benchmarking. Generated by generate_corpus.py. Fixed and versioned.",
        "generator": "tests/graph/corpus/generate_corpus.py",
        "structural": _structural(),
        "semantic": _semantic(),
    }
    (HERE / "graph_corpus.json").write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    s = corpus["structural"]
    sem = corpus["semantic"]
    print(f"structural: {len(s['nodes'])} nodes, {s['cliques']} cliques")
    print(f"semantic: {len(sem['entities'])} entities, {sem['topics']} topics, {len(sem['edges'])} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
