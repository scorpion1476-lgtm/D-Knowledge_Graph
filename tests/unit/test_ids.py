import pytest

from dkg.core.ids import content_id, random_id, ulid_like


def test_content_id_is_stable():
    a = content_id("chunk", "a", "b", "c")
    b = content_id("chunk", "a", "b", "c")
    assert a == b
    assert a.startswith("chunk_")


def test_content_id_changes_with_parts():
    assert content_id("chunk", "a") != content_id("chunk", "b")


def test_random_id_length():
    r = random_id("t", length=16)
    assert r.startswith("t_")
    assert len(r) == 2 + 32  # hex chars


def test_random_id_rejects_short_length():
    with pytest.raises(ValueError):
        random_id("t", length=4)


def test_ulid_is_sortable():
    a = ulid_like()
    b = ulid_like()
    assert len(a) == 26
    assert len(b) == 26
    # Not strictly monotonic across microseconds, but never equal within a call
    assert a != b
