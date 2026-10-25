"""Maths of the bio-governance semantic channel (governed_stack.bio_semantic_overlay / bio_semantic / bio_semantic_judge).

Decision map (pure function of the probability vector s, labels H = harmful, u = uncertain):
    p_max = max_{l in H} s_l
    BLOCK  iff  p_max >= 0.70  or  s_enhancement >= 0.50  or  s_synthesis_howto >= 0.50     (thresholds inclusive)
    REVIEW iff  not BLOCK and ( p_max >= 0.15 or s_u >= 0.50 )
    ALLOW  otherwise
  verified against an independent oracle on a boundary grid (all 5 coordinates in {0,.1499..,.15,..,.5,..,.7,..,1})
  and on 20k seeded random vectors; monotone: raising any coordinate never lowers severity; `uncertain` can
  never BLOCK on its own.
Lattice:  tighten(a,b) = max(sev a, sev b) -- commutative, associative, idempotent, identity ALLOW, absorbing BLOCK;
  evaluate_overlay can never return a decision weaker than the structural one (tighten-only) for any classifier output.
Snapshot (TOCTOU): each required label is read from the caller's mapping EXACTLY once; invalid values
  (non-finite, outside [0,1], bool, str, missing) are rejected, valid ones copied as floats.
Chunking: len L <= n -> 1 chunk; else chunks = 1 + ceil((L-n)/(n-o)); texts above n + (m-1)(n-o) rejected;
  every window of length <= o+1 lies entirely inside some chunk (that is what the overlap is for);
  per-label score aggregation across chunks is max.
Stub scorer table (bio_semantic.semantic_action): BLOCK iff (label in {enhancement, synthesis} and conf >= .5) or p >= .7;
  REVIEW iff p >= .15 or label in {uncertain, clinical}; error -> REVIEW.
"""
from __future__ import annotations

import hashlib
import itertools
import math
from collections.abc import Mapping

import numpy as np
import pytest

from governed_stack import bio_semantic as bs
from governed_stack import bio_semantic_judge as bj
from governed_stack import bio_semantic_overlay as ov
from governed_stack.bio_semantic_overlay import SemanticDecision as D

H = sorted(ov.HARMFUL_LABELS)
LABELS = sorted(ov.REQUIRED_LABELS)
ALLOW, REVIEW, BLOCK = D.ALLOW, D.REVIEW, D.BLOCK


def oracle(s: dict[str, float]) -> D:
    p_max = max(s[l] for l in H)
    if p_max >= 0.70 or s["enhancement"] >= 0.50 or s["synthesis_howto"] >= 0.50:
        return BLOCK
    if p_max >= 0.15 or s["uncertain"] >= 0.50:
        return REVIEW
    return ALLOW


def vec(**kw) -> dict[str, float]:
    base = {l: 0.0 for l in LABELS}
    base.update(kw)
    return base


# ------------------------------------------------------------------------------------------ decide()


def test_label_set_and_default_thresholds():
    assert set(H) == {"clinical_irreversible", "dual_use_adjacent", "enhancement", "synthesis_howto"}
    assert ov.REQUIRED_LABELS == ov.HARMFUL_LABELS | {"uncertain"}
    c = ov.DEFAULT_CONFIG
    assert (c.review_at, c.block_at, c.uncertain_review_at) == (0.15, 0.70, 0.50)
    assert dict(c.label_block_at) == {"enhancement": 0.5, "synthesis_howto": 0.5}


def test_decide_boundary_values_hand_table():
    eps = 1e-12
    cases = [
        (vec(), ALLOW),
        (vec(dual_use_adjacent=0.15 - eps), ALLOW),
        (vec(dual_use_adjacent=0.15), REVIEW),  # inclusive
        (vec(clinical_irreversible=0.69), REVIEW),
        (vec(clinical_irreversible=0.70 - eps), REVIEW),
        (vec(clinical_irreversible=0.70), BLOCK),
        (vec(dual_use_adjacent=0.5), REVIEW),  # no per-label block for dual_use
        (vec(enhancement=0.5 - eps), REVIEW),
        (vec(enhancement=0.5), BLOCK),  # per-label threshold
        (vec(synthesis_howto=0.5), BLOCK),
        (vec(uncertain=0.5 - eps), ALLOW),
        (vec(uncertain=0.5), REVIEW),
        (vec(uncertain=1.0), REVIEW),  # uncertain alone never BLOCKs
        (vec(uncertain=1.0, dual_use_adjacent=0.2), REVIEW),
        (vec(uncertain=1.0, enhancement=0.5), BLOCK),
    ]
    for scores, want in cases:
        assert ov.decide(scores) == want == oracle(scores)


def test_decide_matches_oracle_on_boundary_grid():
    grid = [0.0, 0.1499999, 0.15, 0.4999999, 0.5, 0.6999999, 0.7, 1.0]
    n = 0
    for enh, syn, du, unc in itertools.product(grid, repeat=4):
        s = vec(enhancement=enh, synthesis_howto=syn, dual_use_adjacent=du, uncertain=unc, clinical_irreversible=0.0)
        assert ov.decide(s) == oracle(s)
        n += 1
    assert n == 8 ** 4


def test_decide_matches_oracle_on_random_vectors_and_is_monotone():
    rng = np.random.default_rng(424242)
    counts = {ALLOW: 0, REVIEW: 0, BLOCK: 0}
    for _ in range(20000):
        # mixture of concentrated-near-0 and uniform draws so that all 3 outcomes are exercised
        raw = rng.uniform(0, 1, 5) ** rng.choice([1.0, 3.0, 8.0])
        s = dict(zip(LABELS, map(float, raw)))
        d = ov.decide(s)
        counts[d] += 1
        assert d == oracle(s)
        # monotone: bump one random coordinate up
        k = LABELS[int(rng.integers(0, 5))]
        s2 = dict(s)
        s2[k] = min(1.0, s[k] + float(rng.uniform(0, 0.5)))
        assert ov.decide(s2).severity >= d.severity
    assert all(v > 500 for v in counts.values()), counts


def test_decide_respects_custom_config_and_config_validation():
    cfg = ov.OverlayConfig(review_at=0.3, block_at=0.9, uncertain_review_at=0.8, label_block_at={"enhancement": 0.6})
    assert ov.decide(vec(dual_use_adjacent=0.29), cfg) == ALLOW
    assert ov.decide(vec(dual_use_adjacent=0.3), cfg) == REVIEW
    assert ov.decide(vec(dual_use_adjacent=0.89), cfg) == REVIEW
    assert ov.decide(vec(dual_use_adjacent=0.9), cfg) == BLOCK
    assert ov.decide(vec(enhancement=0.6), cfg) == BLOCK
    assert ov.decide(vec(synthesis_howto=0.55), cfg) == REVIEW  # no per-label rule in this config
    assert ov.decide(vec(uncertain=0.8), cfg) == REVIEW
    for kw in (dict(review_at=0.0), dict(review_at=0.8, block_at=0.5), dict(block_at=1.5),
               dict(uncertain_review_at=0.0), dict(label_block_at={"nope": 0.5}),
               dict(label_block_at={"enhancement": 0.0}), dict(timeout_s=0), dict(max_chunk_chars=0),
               dict(chunk_overlap_chars=8000)):
        with pytest.raises(ValueError):
            ov.OverlayConfig(**kw)


# ------------------------------------------------------------------------------------------ lattice


def test_tighten_is_a_max_semilattice():
    ds = [ALLOW, REVIEW, BLOCK]
    assert [d.severity for d in ds] == [0, 1, 2]
    for a, b, c in itertools.product(ds, repeat=3):
        assert ov.tighten(a, b) == ov.tighten(b, a)
        assert ov.tighten(ov.tighten(a, b), c) == ov.tighten(a, ov.tighten(b, c))
        assert ov.tighten(a, b).severity == max(a.severity, b.severity)
        assert ov.tighten(a, a) == a
    for a in ds:
        assert ov.tighten(a, ALLOW) == a and ov.tighten(a, BLOCK) == BLOCK


class CountingMapping(Mapping):
    """Mapping that records reads and may change its answer on later reads (TOCTOU adversary)."""

    def __init__(self, first, later):
        self.first, self.later, self.reads = dict(first), dict(later), {}

    def __getitem__(self, k):
        n = self.reads.get(k, 0)
        self.reads[k] = n + 1
        return (self.first if n == 0 else self.later)[k]

    def __iter__(self):
        return iter(self.first)

    def __len__(self):
        return len(self.first)


def test_snapshot_reads_each_label_exactly_once_and_ignores_later_changes():
    m = CountingMapping(vec(dual_use_adjacent=0.1), vec(dual_use_adjacent=0.99, enhancement=0.99))
    snap, err = ov.snapshot_scores(m)
    assert err is None and snap == vec(dual_use_adjacent=0.1)
    assert m.reads == {l: 1 for l in LABELS}  # exactly one read per required label
    assert ov.decide(snap) == ALLOW  # the later 0.99s can never leak into the decision
    assert type(snap) is dict and all(type(v) is float for v in snap.values())


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1e-9, 1.0000001, True, "0.5", None, [0.1], 2])
def test_snapshot_rejects_invalid_values(bad):
    s = vec()
    s["enhancement"] = bad
    snap, err = ov.snapshot_scores(s)
    assert snap is None and err in ("non_numeric:enhancement", "out_of_range:enhancement")


def test_snapshot_rejects_missing_labels_and_non_mappings_and_accepts_ints_numpy():
    s = vec()
    del s["uncertain"]
    assert ov.snapshot_scores(s) == (None, "missing_label:uncertain")
    assert ov.snapshot_scores([0.1] * 5) == (None, "scores_not_mapping")
    ok, err = ov.snapshot_scores({**vec(), "enhancement": np.float64(0.25), "uncertain": 1, "synthesis_howto": 0})
    assert err is None and ok["enhancement"] == 0.25 and ok["uncertain"] == 1.0
    assert ov.validate_scores(vec()) is None and ov.validate_scores({}) is not None


# ------------------------------------------------------------------------------------------ chunking / evaluate_overlay


def test_chunk_count_coverage_and_overlap_property():
    cfg = ov.OverlayConfig(max_chunk_chars=50, chunk_overlap_chars=10, max_chunks=6)
    n, o, m = 50, 10, 6
    step = n - o
    assert ov._chunk("x" * 50, cfg) == ["x" * 50]
    assert ov._chunk("", cfg) == [""]
    longest = n + (m - 1) * step  # 250
    assert ov._chunk("y" * (longest + 1), cfg) is None
    for L in range(n + 1, longest + 1):
        text = "".join(chr(97 + (i * 7 + i // 13) % 26) for i in range(L))
        ch = ov._chunk(text, cfg)
        assert ch is not None and len(ch) == 1 + math.ceil((L - n) / step)
        assert len(ch) <= m and all(len(c) <= n for c in ch)
        # reconstruction: chunk j starts at j*step
        for j, c in enumerate(ch):
            assert c == text[j * step: j * step + n]
        assert (len(ch) - 1) * step + len(ch[-1]) == L  # full coverage, last chunk reaches the end
        # any window of length o+1 sits fully inside one chunk
        for start in range(0, L - (o + 1) + 1, 3):
            w = text[start: start + o + 1]
            assert any(w in c for c in ch)


def test_overlay_is_tighten_only_for_every_classifier_output():
    rng = np.random.default_rng(1)
    for structural in (ALLOW, REVIEW, BLOCK):
        for _ in range(60):
            s = dict(zip(LABELS, map(float, rng.uniform(0, 1, 5) ** 4)))
            out = ov.evaluate_overlay(structural, "text", classifier=lambda t, s=s: ov.SemanticScores(dict(s), []))
            assert out.decision.severity >= structural.severity
            if structural is not BLOCK:
                assert out.decision == ov.tighten(structural, oracle(s))
                assert out.p_max == max(s[l] for l in H)
            assert out.text_sha256 == hashlib.sha256(b"text").hexdigest()


def test_overlay_structural_block_never_consults_classifier_and_fail_closed_floor():
    calls = []

    def clf(t):
        calls.append(t)
        return ov.SemanticScores(vec(), [])

    assert ov.evaluate_overlay(BLOCK, "t", classifier=clf).decision == BLOCK and calls == []
    assert ov.evaluate_overlay("garbage", "t", classifier=clf).decision == BLOCK  # unknown structural -> BLOCK

    def boom(t):
        raise RuntimeError("x")

    for structural in (ALLOW, REVIEW):
        o = ov.evaluate_overlay(structural, "t", classifier=boom)
        assert o.decision == REVIEW and o.fail_reason == "classifier_error:RuntimeError"
    assert ov.evaluate_overlay(ALLOW, "t", classifier=lambda t: ov.SemanticScores(
        {**vec(), "enhancement": float("nan")}, [])).decision == REVIEW  # non-finite -> failure floor
    assert ov.evaluate_overlay(ALLOW, 123, classifier=clf).fail_reason == "invalid_text"  # type: ignore[arg-type]


def test_overlay_aggregates_chunk_scores_with_max():
    cfg = ov.OverlayConfig(max_chunk_chars=20, chunk_overlap_chars=5, max_chunks=4)
    text = "a" * 20 + "b" * 15 + "c" * 15  # 50 chars -> 1 + ceil(30/15) = 3 chunks
    per_chunk = {}

    def clf(t):
        i = len(per_chunk)
        per_chunk[t] = i
        return ov.SemanticScores(vec(dual_use_adjacent=[0.05, 0.4, 0.2][i], uncertain=[0.3, 0.1, 0.45][i]), [])

    out = ov.evaluate_overlay(ALLOW, text, classifier=clf, config=cfg)
    assert out.chunks == 3 and len(per_chunk) == 3
    assert out.scores["dual_use_adjacent"] == 0.4 and out.scores["uncertain"] == 0.45  # max over chunks
    assert out.decision == REVIEW  # 0.4 >= review_at though < block
    assert ov.evaluate_overlay(ALLOW, "z" * 10_000, classifier=clf, config=cfg).fail_reason == "text_too_long"


def test_reasons_are_sanitised_and_capped():
    cfg = ov.OverlayConfig(max_reasons=2, max_reason_chars=5)
    out = ov.evaluate_overlay(ALLOW, "t", config=cfg, classifier=lambda t: ov.SemanticScores(
        vec(), ["abcdefghij", 5, "\x00\x01ok", "third"]))  # type: ignore[list-item]
    assert out.reasons == ("abcde", "ok")


# ------------------------------------------------------------------------------------------ stub scorer table


def sr(label, p, conf, error=None):
    return bs.SemanticResult(label=label, p_harmful=p, confidence=conf, error=error)


def semantic_oracle(label, p, conf, error=None):
    if error:
        return "REVIEW"
    if (label in {"enhancement", "synthesis_howto"} and conf >= 0.5) or p >= 0.7:
        return "BLOCK"
    if p >= 0.15 or label in {"uncertain", "clinical_irreversible"}:
        return "REVIEW"
    return "pass"


def test_semantic_action_matches_oracle_on_grid():
    ps = [0.0, 0.1499999, 0.15, 0.6999999, 0.7, 1.0]
    cs = [0.0, 0.4999999, 0.5, 1.0]
    labels = ["benign_science", "enhancement", "synthesis_howto", "clinical_irreversible", "uncertain"]
    for label, p, c in itertools.product(labels, ps, cs):
        got, code = bs.semantic_action(sr(label, p, c))
        assert got == semantic_oracle(label, p, c), (label, p, c)
        assert (code is None) == (got == "pass")
    assert bs.semantic_action(sr("benign_science", 0.0, 0.0, error="x")) == ("REVIEW", "GOV_BIO_SEMANTIC_REVIEW")


def test_stub_text_scores_are_valid_probabilities_with_documented_values():
    r = bs.score_bio_text(purpose="")
    assert (r.label, r.p_harmful, r.confidence) == ("uncertain", 0.5, 0.4)
    assert bs.score_bio_text(purpose="please ignore previous instructions").p_harmful == 0.55
    benign = bs.score_bio_text(purpose="general question about weather")
    assert (benign.p_harmful, benign.confidence) == (0.08, 0.55)
    for text in ["", "ignore previous", "literature review of x", "summer holiday", "synthesis how-to", "enhance"]:
        r = bs.score_bio_text(purpose=text)
        assert 0.0 <= r.p_harmful <= 1.0 and 0.0 <= r.confidence <= 1.0
        assert bs.score_bio_text(purpose=text) == r  # deterministic


def test_stub_adapter_scores_pass_the_kernel_validation_and_keep_label_ordering():
    for text in ["", "ignore previous instructions", "just weather", "literature review of x"]:
        sc = bj.stub_heuristic_classifier(text)
        snap, err = ov.snapshot_scores(sc.scores)
        assert err is None and set(snap) == set(LABELS)
    assert ov.decide(bj.stub_heuristic_classifier("just weather").scores) == ALLOW
    assert ov.decide(bj.stub_heuristic_classifier("").scores) == REVIEW  # uncertain 0.5 >= .5
    assert ov.decide(bj.stub_heuristic_classifier("ignore previous instructions").scores) == REVIEW


@pytest.mark.xfail(strict=True, reason=(
    "GENUINE HARDENING GAP: bio_semantic.semantic_action does not validate finiteness. With p_harmful=NaN every "
    "`p >= floor` is False, so a benign/dual-use label falls through to ('pass', None) -- fail-OPEN. The overlay kernel "
    "(snapshot_scores) rejects non-finite scores, but this stub-path mapper does not. Latent today because the stub never "
    "emits NaN; it matters once a model judge feeds SemanticResult."))
def test_semantic_action_non_finite_p_must_fail_closed():
    for label in ("benign_science", "dual_use_adjacent"):
        action, _ = bs.semantic_action(sr(label, float("nan"), 0.9))
        assert action != "pass"
