from dkg.extract.claims import extract_claims


def test_is_predicate():
    claims = extract_claims("Alpha is fast and reliable.")
    assert claims
    assert claims[0].predicate == "is"


def test_reports_predicate():
    claims = extract_claims("Beta reports substantial improvements.")
    kinds = {c.predicate for c in claims}
    assert "reports" in kinds


def test_no_claims_from_short_or_odd_text():
    assert extract_claims("Hi!") == []


def test_dedup_within_document():
    claims = extract_claims("Alpha is fast. Alpha is fast.")
    assert len(claims) == 1


def test_heading_first_document_still_yields_the_claim_below_it():
    # The defect this covers: the sentence splitter glued a heading with no
    # terminal punctuation to the first real sentence, so every pattern, which
    # is anchored at the start of the segment, failed to match.
    text = "# Runbook for service 0\n\nThe cache TTL for service 0 is 30 seconds.\n"
    claims = extract_claims(text)
    assert [(c.predicate, c.subject_hint, c.object_text) for c in claims] == [
        ("is", "The cache TTL for service 0", "30 seconds")
    ]


def test_hard_wrapped_lines_in_one_block_are_joined():
    text = "# Note\n\nService 0 uses a cache TTL of 300 seconds, chosen to reduce\nload on the store.\n"
    claims = extract_claims(text)
    assert claims[0].predicate == "uses"
    assert claims[0].object_text.endswith("reduce load on the store")


def test_list_items_are_separate_claims_not_one_run_on_segment():
    text = "# Limits\n\n- The maximum upload size is 10 megabytes.\n- The retry budget is 3 attempts.\n"
    predicates = [(c.subject_hint, c.object_text) for c in extract_claims(text)]
    assert ("The maximum upload size", "10 megabytes") in predicates
    assert ("The retry budget", "3 attempts") in predicates


def test_fenced_code_is_not_read_as_prose():
    text = "# Doc\n\n```python\nAlpha is fast and reliable.\n```\n\nBeta is slow.\n"
    subjects = {c.subject_hint for c in extract_claims(text)}
    assert subjects == {"Beta"}


def test_setext_heading_underline_is_not_prose():
    text = "Service overview\n================\n\nThe endpoint is public.\n"
    claims = extract_claims(text)
    assert [(c.subject_hint, c.object_text) for c in claims] == [("The endpoint", "public")]


def test_markdown_segmentation_did_not_change_plain_text_behaviour():
    """Multi-paragraph plain text with no markers: previously one segment run,
    now one block per paragraph. Neither shape may change what is extracted."""
    text = "Alpha is fast and reliable.\n\nBeta is slow.\n\nAlpha is fast and reliable."
    claims = [(c.subject_hint, c.object_text) for c in extract_claims(text)]
    assert claims == [("Alpha", "fast and reliable"), ("Beta", "slow")]
