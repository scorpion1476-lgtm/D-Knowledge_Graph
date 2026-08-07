from dkg.evidence.confidence import ConfidenceInputs, score_confidence


def test_score_within_bounds():
    r = score_confidence(ConfidenceInputs(0.5, 3, 0, 30))
    assert 0.0 <= r.score <= 1.0
    assert "raw" in r.explain


def test_more_support_raises_score():
    a = score_confidence(ConfidenceInputs(0.5, 0, 0, 0))
    b = score_confidence(ConfidenceInputs(0.5, 10, 0, 0))
    assert b.score > a.score


def test_contradiction_lowers_score():
    a = score_confidence(ConfidenceInputs(0.5, 5, 0, 0))
    b = score_confidence(ConfidenceInputs(0.5, 5, 5, 0))
    assert b.score < a.score
