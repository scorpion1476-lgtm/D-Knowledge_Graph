from dkg.extract.dedupe import cosine_shingles, jaccard


def test_jaccard_identical():
    assert jaccard("hello world", "hello world") == 1.0


def test_jaccard_disjoint():
    assert jaccard("alpha", "beta") == 0.0


def test_shingle_similarity_scales():
    a = "the quick brown fox jumps over the lazy dog"
    b = "the quick brown fox jumps over a lazy cat"
    c = "completely different sentence about physics"
    assert cosine_shingles(a, b, n=3) > cosine_shingles(a, c, n=3)
