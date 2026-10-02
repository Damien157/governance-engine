import itertools
import random
import threading

import pytest

import bio_semantic_overlay as m
from bio_semantic_overlay import (
    OverlayConfig, SemanticDecision as D, SemanticScores,
    evaluate_overlay, semantic_overlay,
)

ALLOW, REVIEW, BLOCK = D.ALLOW, D.REVIEW, D.BLOCK
ALL = [ALLOW, REVIEW, BLOCK]


def scores(**over):
    base = {l: 0.0 for l in m.REQUIRED_LABELS}
    base.update(over)
    return base


def fake(**over):
    return lambda text: SemanticScores(scores(**over), ["ok"])


# ---- structural handling --------------------------------------------------

def test_structural_block_skips_classifier():
    calls = []

    def clf(text):
        calls.append(text)
        return SemanticScores(scores(), [])

    o = evaluate_overlay(BLOCK, "x", classifier=clf)
    assert o.decision is BLOCK and calls == []


@pytest.mark.parametrize("bad", [None, "BLOCK", 2, object()])
def test_unknown_structural_blocks(bad):
    assert evaluate_overlay(bad, "x", classifier=fake()).decision is BLOCK


# ---- thresholds -----------------------------------------------------------

@pytest.mark.parametrize("over,expected", [
    ({}, ALLOW),
    ({"dual_use_adjacent": 0.1499}, ALLOW),
    ({"dual_use_adjacent": 0.15}, REVIEW),
    ({"clinical_irreversible": 0.6999}, REVIEW),
    ({"clinical_irreversible": 0.70}, BLOCK),
    ({"enhancement": 0.4999}, REVIEW),
    ({"enhancement": 0.50}, BLOCK),
    ({"synthesis_howto": 0.4999}, REVIEW),
    ({"synthesis_howto": 0.50}, BLOCK),
    ({"uncertain": 0.4999}, ALLOW),
    ({"uncertain": 0.50}, REVIEW),
])
def test_thresholds(over, expected):
    assert evaluate_overlay(ALLOW, "x", classifier=fake(**over)).decision is expected


# ---- fail closed ----------------------------------------------------------

def _bad_score_sets():
    good = scores()
    yield {k: v for k, v in good.items() if k != "enhancement"}      # missing
    yield {k: v for k, v in good.items() if k != "uncertain"}
    yield scores(enhancement=float("nan"))
    yield scores(dual_use_adjacent=float("inf"))
    yield scores(enhancement=-0.1)
    yield scores(enhancement=1.1)
    yield scores(enhancement=None)
    yield scores(enhancement="0.9")
    yield scores(enhancement=True)
    yield None
    yield [0.1, 0.2]


@pytest.mark.parametrize("bad", list(_bad_score_sets()))
@pytest.mark.parametrize("structural", [ALLOW, REVIEW])
def test_invalid_scores_fail_closed(bad, structural):
    o = evaluate_overlay(structural, "x",
                         classifier=lambda t: SemanticScores(bad, []))
    assert o.decision is REVIEW and o.fail_reason.startswith("invalid_scores")


def test_classifier_exception_and_stub():
    def boom(t):
        raise RuntimeError("secret text in message")

    o = evaluate_overlay(ALLOW, "x", classifier=boom)
    assert o.decision is REVIEW and o.fail_reason == "classifier_error:RuntimeError"
    assert "secret" not in o.fail_reason

    o = evaluate_overlay(ALLOW, "x")  # real stub
    assert o.decision is REVIEW and o.fail_reason == "classifier_not_implemented"


def test_timeout_fails_closed():
    release = threading.Event()

    def slow(t):
        release.wait(5)
        return SemanticScores(scores(), [])

    try:
        o = evaluate_overlay(ALLOW, "x", classifier=slow,
                             config=OverlayConfig(timeout_s=0.05))
        assert o.decision is REVIEW and o.fail_reason == "timeout"
    finally:
        release.set()


def test_non_string_text():
    o = evaluate_overlay(ALLOW, None, classifier=fake())
    assert o.decision is REVIEW and o.fail_reason == "invalid_text"


# ---- chunking -------------------------------------------------------------

def test_harm_in_last_chunk_is_caught():
    cfg = OverlayConfig(max_chunk_chars=100, chunk_overlap_chars=10, max_chunks=5)
    text = "a" * 250 + "TAIL"

    def clf(t):
        hit = "TAIL" in t
        return SemanticScores(scores(synthesis_howto=0.9 if hit else 0.0), [])

    o = evaluate_overlay(ALLOW, text, classifier=clf, config=cfg)
    assert o.chunks > 1 and o.decision is BLOCK


def test_overlap_catches_boundary_straddle():
    cfg = OverlayConfig(max_chunk_chars=100, chunk_overlap_chars=20, max_chunks=5)
    text = "a" * 90 + "NEEDLE" + "b" * 100  # straddles the first boundary

    def clf(t):
        return SemanticScores(scores(enhancement=0.9 if "NEEDLE" in t else 0.0), [])

    assert evaluate_overlay(ALLOW, text, classifier=clf, config=cfg).decision is BLOCK


def test_too_long_fails_closed():
    cfg = OverlayConfig(max_chunk_chars=100, chunk_overlap_chars=10, max_chunks=2)
    o = evaluate_overlay(ALLOW, "a" * 1000, classifier=fake(), config=cfg)
    assert o.decision is REVIEW and o.fail_reason == "text_too_long"


# ---- audit / hygiene ------------------------------------------------------

def test_reasons_sanitised_and_no_raw_text():
    long_r = "x" * 1000
    clf = lambda t: SemanticScores(scores(), ["ok\x00\x1b[31m", long_r, 7, ""] + ["r"] * 20)
    o = evaluate_overlay(ALLOW, "sensitive input", classifier=clf)
    assert len(o.reasons) == 5
    assert all(r.isprintable() and len(r) <= 200 for r in o.reasons)
    assert "sensitive" not in repr(o)
    assert len(o.text_sha256) == 64


# ---- properties -----------------------------------------------------------

def test_tighten_only_property():
    rng = random.Random(0)
    junk = [float("nan"), float("inf"), -1.0, 2.0, None, "x", True]
    for _ in range(2000):
        s = {l: rng.choice([rng.random(), rng.random() * 0.2, 0.0, 0.15, 0.5, 0.7, *junk])
             for l in m.REQUIRED_LABELS}
        for l in list(s):
            if rng.random() < 0.05:
                del s[l]
        for structural in ALL:
            o = evaluate_overlay(structural, "x",
                                 classifier=lambda t: SemanticScores(s, []))
            assert o.decision.severity >= structural.severity


def test_monotone_in_scores():
    """Raising any score never loosens the decision."""
    rng = random.Random(1)
    for _ in range(500):
        s = {l: rng.random() for l in m.REQUIRED_LABELS}
        base = m.decide(s)
        for l in m.REQUIRED_LABELS:
            hi = dict(s)
            hi[l] = min(1.0, s[l] + rng.random())
            assert m.decide(hi).severity >= base.severity


def test_wrapper_matches_original_api():
    assert semantic_overlay(BLOCK, "x") is BLOCK
    assert semantic_overlay(ALLOW, "x") is REVIEW  # stub -> fail closed


# ---- config ---------------------------------------------------------------

@pytest.mark.parametrize("kw", [
    {"review_at": 0.8, "block_at": 0.7},
    {"review_at": 0.0},
    {"timeout_s": 0},
    {"chunk_overlap_chars": 8000},
    {"label_block_at": {"bogus": 0.5}},
])
def test_config_validation(kw):
    with pytest.raises(ValueError):
        OverlayConfig(**kw)
