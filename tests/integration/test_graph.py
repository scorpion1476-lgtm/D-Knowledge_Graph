from dkg.graph.query import neighbourhood
from dkg.ingest.base import ingest_text


def test_neighbourhood_returns_entity_and_edges(db):
    ingest_text(
        db,
        "Alpha Corp. is based in Berlin. Beta Labs Ltd. is in Berlin. Berlin is a city.",
        display_name="d",
    )
    # Look for any entity we might have extracted; entity IDs are content-derived.
    ent = db.fetchone("SELECT canonical FROM entities WHERE kind='organisation' LIMIT 1;")
    if ent is None:
        # fallback: search for a person-like entity
        ent = db.fetchone("SELECT canonical FROM entities LIMIT 1;")
    assert ent is not None
    nb = neighbourhood(db, ent["canonical"], depth=1, max_nodes=50)
    assert nb["root"] is not None
    assert isinstance(nb["nodes"], list)
    assert "algorithm" in nb["why"]


def test_neighbourhood_handles_missing_entity(db):
    nb = neighbourhood(db, "nonexistent", depth=2)
    assert nb["root"] is None
    assert nb["nodes"] == []
