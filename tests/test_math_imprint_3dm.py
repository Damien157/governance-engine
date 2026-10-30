"""Maths of imprint 3DM (imprint/src/imprint): "weighted classical draw over matchings".

Equations verified:

  Space        S = all k-subsets of T,  |S| = C(|T|, k);   L = {M in S : V(M) = 1}
  Validity     V(M) = 1  iff  |M| = k and any two triples differ in all three coordinates
               (verified with an independent PAIRWISE formulation, not the set-size one used in the code)
  Soft score   s(M) = (|X(M)|/k)(|Y(M)|/k)(|Z(M)|/k)  in (0,1],  = 1 iff V(M) = 1
  Weights      naive:    w(M) = alpha s(M) + beta F(M)                (gamma = 0)
               reversed: w(M) = alpha s(M) + beta F(M) + gamma g(M)
               F(M) = #planted triples in M;  g(M) = (1-m) p(M) + m p(M) peel(M)
               peel(M) = 1/2 + 1/2 (#unused triples conflicting with M)/(#unused),  0 if V(M)=0
  Born rule    alpha_M = sqrt(w_M / sum w),   Pr[M] = |alpha_M|^2 = w_M / sum w,   sum_M Pr[M] = 1
  Greedy prior p(M) = #{orderings of T on which greedy returns M} / |T|!   (exact for |T| <= 10)
               hand-solvable case below: 3 triples a,b,c with a,b disjoint and c conflicting with both
               => P({a,b}) = 4/6, P(None) = 2/6.
  Sampling     empirical frequencies -> Pr[M]  (chi-square with a fixed seed, plus a 5-sigma envelope)
  Baselines    P_uniform(V=1) = |L|/|S|;  E[#draws to first valid] = |S|/|L| (geometric)
  DNA          codon length  L_c = max(2, ceil(log_3 n)),  triple length 3 L_c,
               |E_DNA(M)| = 3 L_c k + 4 * (#triples reusing a coordinate seen earlier, in sorted order),
               clash motif TTTT present  iff  V(M) = 0.

All stochastic checks use fixed seeds.  Nothing here claims quantum speed-up: the draw is classical.
"""
from __future__ import annotations

import math
import random
from itertools import combinations, permutations

import numpy as np
import pytest
from imprint import (
    DNACodec,
    SoftVerifier,
    SuperpositionSampler,
    generate_demo,
    generate_planted_no,
    generate_planted_yes,
    generate_random,
    hard_valid,
    matching_key,
)
from imprint.baselines import (
    first_valid_uniform_expected,
    greedy_matching,
    greedy_prior,
    greedy_prior_distribution,
    greedy_randomized_hit_rate,
    peel_score,
    uniform_hit_probability,
)
from imprint.dna import CLASH_MOTIF
from imprint.enumerator import enumerate_S_L
from imprint.instance import Instance3DM, catalog
from imprint.metrics import time_to_first_valid_uniform
from imprint.verifier import feature_vector, fitness, handcrafted_score
from scipy import stats

SEED = 3_141_592


def pairwise_valid(M, k):
    """Independent V: k distinct triples, any two differ in x, y AND z."""
    M = list(M)
    if len(M) != k or len(set(M)) != k:
        return False
    return all(a[i] != b[i] for a, b in combinations(M, 2) for i in range(3))


def instances():
    return [
        generate_demo(),
        generate_planted_yes(n=5, n_extra=7, k=3, seed=0),
        generate_planted_no(n=5, n_triples=10, k=3, seed=2),
        generate_random(n=4, n_triples=8, k=3, seed=3),
    ]


# ------------------------------------------------------------------ S, L, V


@pytest.mark.parametrize("inst", instances(), ids=lambda i: i.name)
def test_S_size_is_binomial_and_L_matches_independent_validity(inst):
    S, L = enumerate_S_L(inst)
    assert len(S) == math.comb(len(inst.T), inst.k)
    assert len({frozenset(M) for M in S}) == len(S)  # all distinct
    L_indep = [M for M in S if pairwise_valid(M, inst.k)]
    assert {frozenset(M) for M in L} == {frozenset(M) for M in L_indep}
    for M in S:
        assert hard_valid(M, inst.k) == pairwise_valid(M, inst.k)


def test_hard_valid_edge_cases():
    assert not hard_valid([], 1)
    assert not hard_valid([(0, 1, 2), (0, 1, 2)], 2)  # duplicate triple
    assert not hard_valid([(0, 1, 2), (3, 4, 2)], 2)  # shared z
    assert hard_valid([(0, 1, 2), (3, 4, 5)], 2)
    assert not hard_valid([(0, 1, 2)], 2)  # wrong size


def test_demo_numbers():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    assert (len(inst.T), len(S), len(L)) == (9, 84, 9)
    assert pairwise_valid(inst.planted, 3)


def test_planted_yes_contains_planted_and_planted_no_has_no_matching_by_pigeonhole():
    yes = generate_planted_yes(n=5, n_extra=7, k=3, seed=0)
    assert frozenset(yes.planted) in {frozenset(M) for M in enumerate_S_L(yes)[1]}
    no = generate_planted_no(n=5, n_triples=10, k=3, seed=2)
    xs = {t[0] for t in no.T}
    assert len(xs) == no.k - 1  # only k-1 distinct X elements appear => max matching < k
    assert enumerate_S_L(no)[1] == []
    # independent max-matching size via brute force over all sub-families
    best = 0
    for r in range(1, no.k + 1):
        if any(pairwise_valid(c, r) for c in combinations(no.T, r)):
            best = r
    assert best < no.k


def test_catalog_instances_have_consistent_shapes():
    for inst in catalog():
        inst.validate_shape()
        S, L = enumerate_S_L(inst)
        assert len(S) == math.comb(len(inst.T), inst.k)
        assert len(L) <= len(S)


def test_instance_shape_validation():
    base = generate_demo()
    with pytest.raises(ValueError):
        Instance3DM(X=base.X, Y=base.Y, Z=base.Z, T=base.T, k=0).validate_shape()
    with pytest.raises(ValueError):
        Instance3DM(X=base.X, Y=base.Y, Z=base.Z, T=base.T[:2], k=3).validate_shape()
    with pytest.raises(ValueError):
        Instance3DM(X=(0,), Y=(1,), Z=(2,), T=((9, 1, 2),), k=1).validate_shape()


# ------------------------------------------------------------------ soft scores


def test_handcrafted_score_formula_and_unity_iff_valid():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    k = inst.k
    for M in S:
        xs, ys, zs = zip(*M)
        expect = (len(set(xs)) / k) * (len(set(ys)) / k) * (len(set(zs)) / k)
        assert handcrafted_score(M, k) == pytest.approx(expect, abs=1e-15)
        assert (handcrafted_score(M, k) == 1.0) == pairwise_valid(M, k)
        assert 0.0 < handcrafted_score(M, k) <= 1.0
    assert handcrafted_score([], 3) == 0.0 and handcrafted_score([(0, 1, 2)], 0) == 0.0


def test_feature_vector_pairs_sum_to_one():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    for M in S:
        f = feature_vector(M, inst.k)
        np.testing.assert_allclose(f[:3] + f[3:], 1.0, atol=1e-15)
        assert np.all((0 <= f) & (f <= 1))


def test_soft_verifier_trained_logistic_regression_reduces_cross_entropy():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    k = inst.k
    X = np.stack([feature_vector(M, k) for M in S])
    y = np.array([1.0 if pairwise_valid(M, k) else 0.0 for M in S])
    Phi = np.hstack([X, np.ones((len(S), 1))])

    def xent(w):
        p = 1 / (1 + np.exp(-np.clip(Phi @ w, -30, 30)))
        return float(-np.mean(y * np.log(p + 1e-15) + (1 - y) * np.log(1 - p + 1e-15)))

    sv = SoftVerifier()
    info = sv.train_on_instance(inst, S)
    assert not info["degenerate"] and info["pos"] == int(y.sum())
    w0 = np.random.default_rng(0).normal(0.0, 0.1, size=Phi.shape[1])  # the code's own init
    assert xent(sv.w) < xent(w0)
    # logistic-regression gradient at the solution is smaller than at init
    def grad(w):
        p = 1 / (1 + np.exp(-np.clip(Phi @ w, -30, 30)))
        return Phi.T @ (p - y) / len(y)
    assert np.linalg.norm(grad(sv.w)) < np.linalg.norm(grad(w0))
    probs = np.array([sv.predict_proba(M, k) for M in S])
    assert np.all((probs > 0) & (probs < 1))
    assert probs[y == 1].mean() > probs[y == 0].mean()
    # soft_score is the convex mix of trained probability and handcrafted product
    for mix in (0.0, 0.3, 1.0):
        M = S[5]
        assert sv.soft_score(M, k, mix=mix) == pytest.approx(
            mix * sv.predict_proba(M, k) + (1 - mix) * handcrafted_score(M, k), abs=1e-14)


def test_untrained_soft_verifier_falls_back_to_handcrafted():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    sv = SoftVerifier()
    for M in S[:20]:
        assert sv.predict_proba(M, inst.k) == handcrafted_score(M, inst.k)


# ------------------------------------------------------------------ weights, Born rule


def test_naive_weights_are_alpha_s_plus_beta_F_and_born_rule_normalisation():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    alpha, beta = 1.0, 0.25
    sampler = SuperpositionSampler(inst, S, SoftVerifier(), alpha=alpha, beta=beta, mode="naive")
    planted = set(inst.planted)
    w = np.array([alpha * handcrafted_score(M, inst.k) + beta * sum(t in planted for t in M) for M in S])
    np.testing.assert_allclose(sampler.weights, w, atol=1e-14)
    np.testing.assert_allclose(sampler.probs, w / w.sum(), atol=1e-15)
    np.testing.assert_allclose(sampler.amplitudes**2, sampler.probs, atol=1e-15)
    assert sampler.probs.sum() == pytest.approx(1.0, abs=1e-14)
    assert np.all(sampler.probs >= 0)
    assert sum(abs(a) ** 2 for a in sampler.hilbert_dict().values()) == pytest.approx(1.0, abs=1e-14)
    assert sampler.gamma == 0.0  # naive forces gamma = 0


def test_reversed_weights_add_gamma_times_greedy_prior():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    dist = greedy_prior_distribution(inst)
    sv = SoftVerifier()
    gamma, peel_mix = 100.0, 0.15
    sampler = SuperpositionSampler(inst, S, sv, mode="reversed", gamma=gamma, peel_mix=peel_mix, greedy_dist=dist)
    planted = set(inst.planted)
    for M, wi in zip(S, sampler.weights):
        key = matching_key(M)
        p = dist.get(key, 0.0)
        peel = 0.0
        if pairwise_valid(key, inst.k):
            used = [set(t[i] for t in key) for i in range(3)]
            unused = [t for t in inst.T if t not in set(key)]
            skipped = sum(any(t[i] in used[i] for i in range(3)) for t in unused)
            peel = 0.5 + 0.5 * skipped / len(unused) if unused else 1.0
        g = (1 - peel_mix) * p + peel_mix * (p * peel if p > 0 else 0.0)
        expect = 1.0 * handcrafted_score(M, inst.k) + 0.25 * sum(t in planted for t in M) + gamma * g
        assert wi == pytest.approx(expect, abs=1e-12)
    assert sampler.probs.sum() == pytest.approx(1.0, abs=1e-14)


def test_amplitude_amplification_reweight_formula():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    base = SuperpositionSampler(inst, S, SoftVerifier(), mode="naive")
    amp = SuperpositionSampler(inst, S, SoftVerifier(), mode="naive", amp_rounds=2, amp_gamma=1.5)
    scores = np.clip([handcrafted_score(M, inst.k) for M in S], 1e-9, None)
    np.testing.assert_allclose(amp.weights, base.weights * scores ** (1.5 * 2), rtol=1e-12)


def test_hit_probability_matches_sum_over_L_and_planted_no_is_zero():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    sampler = SuperpositionSampler(inst, S, SoftVerifier(), mode="naive")
    L_labels = {matching_key(M) for M in L}
    manual = sum(p for M, p in zip(S, sampler.probs) if matching_key(M) in L_labels)
    assert sampler.hit_probability(L_labels) == pytest.approx(manual, abs=1e-14)
    assert 0.0 <= sampler.hit_probability(L_labels) <= 1.0
    # weights prefer valid matchings: hit prob >= uniform |L|/|S| (s(M) = 1 on L, < 1 elsewhere)
    assert sampler.hit_probability(L_labels) >= len(L) / len(S) - 1e-12
    assert sampler.hit_probability(set()) == 0.0


# ------------------------------------------------------------------ empirical frequencies


def test_collapse_is_reproducible_and_equals_numpy_choice_stream():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    sampler = SuperpositionSampler(inst, S, SoftVerifier(), mode="naive")
    a = sampler.sample_many(300, seed=5)
    b = sampler.sample_many(300, seed=5)
    assert a == b
    rng = np.random.default_rng(5)
    manual = [sampler.labels[int(rng.choice(len(sampler.labels), p=sampler.probs))] for _ in range(300)]
    assert a == manual
    assert a != sampler.sample_many(300, seed=6)


def test_empirical_frequencies_converge_chi_square_and_envelope():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    sampler = SuperpositionSampler(inst, S, SoftVerifier(), mode="naive")
    n = 30_000
    draws = sampler.sample_many(n, seed=SEED)
    idx = {lab: i for i, lab in enumerate(sampler.labels)}
    counts = np.zeros(len(S))
    for d in draws:
        counts[idx[d]] += 1
    p = sampler.probs
    # 5-sigma binomial envelope per outcome
    sd = np.sqrt(n * p * (1 - p))
    assert np.all(np.abs(counts - n * p) <= 5 * sd + 1)
    # Pearson chi-square after pooling cells with expected count < 10
    exp_ = n * p
    big = exp_ >= 10
    obs_f, exp_f = counts[big], exp_[big]
    if (~big).any():
        obs_f = np.append(obs_f, counts[~big].sum())
        exp_f = np.append(exp_f, exp_[~big].sum())
    exp_f = exp_f * (obs_f.sum() / exp_f.sum())
    chi2, pval = stats.chisquare(obs_f, exp_f)
    assert pval > 1e-3, (chi2, pval)  # fixed seed: deterministic outcome
    # hit rate: empirical fraction in L within 5 sigma of Pr[L]
    L_labels = {matching_key(M) for M in L}
    pl = sampler.hit_probability(L_labels)
    emp = sum(d in L_labels for d in draws) / n
    assert abs(emp - pl) <= 5 * math.sqrt(pl * (1 - pl) / n)


# ------------------------------------------------------------------ greedy prior / baselines


def _tiny_greedy_instance():
    X, Y, Z = (0, 1), (2, 3), (4, 5)
    a, b, c = (0, 2, 4), (1, 3, 5), (0, 3, 4)
    return Instance3DM(X=X, Y=Y, Z=Z, T=(a, b, c), k=2), a, b, c


def test_greedy_prior_distribution_hand_solved_case():
    inst, a, b, c = _tiny_greedy_instance()
    # By hand (6 orderings): [a,b,c],[a,c,b],[b,a,c],[b,c,a] -> {a,b};  [c,a,b],[c,b,a] -> None.
    dist = greedy_prior_distribution(inst)
    assert dist == {matching_key([a, b]): pytest.approx(4 / 6)}
    assert sum(dist.values()) == pytest.approx(2 / 3)
    assert greedy_matching(inst, order=[c, a, b]) is None
    # randomised greedy hit rate converges to 2/3 (fixed seed, 5-sigma binomial envelope)
    n = 20_000
    rate = greedy_randomized_hit_rate(inst, n, seed=SEED)
    assert abs(rate - 2 / 3) <= 5 * math.sqrt((2 / 3) * (1 / 3) / n)


def test_greedy_prior_distribution_matches_independent_enumeration():
    inst = generate_planted_yes(n=4, n_extra=3, k=3, seed=1)  # |T| = 6 -> 720 orderings
    expect = {}
    nperm = 0
    for order in permutations(inst.T):
        nperm += 1
        used = [set(), set(), set()]
        chosen = []
        for t in order:
            if all(t[i] not in used[i] for i in range(3)):
                chosen.append(t)
                for i in range(3):
                    used[i].add(t[i])
                if len(chosen) == inst.k:
                    break
        if len(chosen) == inst.k:
            key = tuple(sorted(chosen))
            expect[key] = expect.get(key, 0) + 1
    got = greedy_prior_distribution(inst)
    assert set(got) == set(expect)
    for key, cnt in expect.items():
        assert got[key] == pytest.approx(cnt / nperm, abs=1e-15)
    # every emitted matching is hard-valid; prior is 0 on invalids
    for M in enumerate_S_L(inst)[0]:
        if not pairwise_valid(M, inst.k):
            assert greedy_prior(M, inst, got) == 0.0


def test_greedy_prior_monte_carlo_branch_is_consistent_with_exact():
    inst = generate_planted_yes(n=4, n_extra=3, k=3, seed=1)
    exact = greedy_prior_distribution(inst)
    mc = greedy_prior_distribution(inst, max_exact=0, sample_perms=40_000, seed=SEED)
    for key, p in exact.items():
        assert mc.get(key, 0.0) == pytest.approx(p, abs=5 * math.sqrt(p * (1 - p) / 40_000) + 1e-9)


def test_peel_score_formula_bounds():
    inst = generate_demo()
    S, _ = enumerate_S_L(inst)
    for M in S:
        s = peel_score(M, inst)
        if not pairwise_valid(M, inst.k):
            assert s == 0.0
            continue
        key = set(M)
        used = [set(t[i] for t in key) for i in range(3)]
        unused = [t for t in inst.T if t not in key]
        skipped = sum(any(t[i] in used[i] for i in range(3)) for t in unused)
        assert s == pytest.approx(0.5 + 0.5 * skipped / len(unused), abs=1e-15)
        assert 0.5 <= s <= 1.0


def test_uniform_baseline_closed_forms():
    assert uniform_hit_probability(84, 9) == pytest.approx(9 / 84)
    assert first_valid_uniform_expected(84, 9) == pytest.approx(84 / 9)
    assert uniform_hit_probability(0, 0) == 0.0
    assert math.isinf(first_valid_uniform_expected(10, 0))


def test_time_to_first_valid_uniform_matches_geometric_mean():
    n_S, n_L, trials = 84, 9, 4000
    p = n_L / n_S
    mean, median = time_to_first_valid_uniform(n_S, n_L, trials, seed=SEED)
    se = math.sqrt(1 - p) / p / math.sqrt(trials)  # std of the mean of a geometric(p)
    assert abs(mean - 1 / p) <= 5 * se
    assert median == pytest.approx(math.ceil(math.log(0.5) / math.log(1 - p)), abs=1.0)
    assert time_to_first_valid_uniform(10, 0, 5, seed=1) == (float("inf"), float("inf"))


def test_fitness_counts_planted_triples():
    inst = generate_demo()
    for M in enumerate_S_L(inst)[0]:
        assert fitness(M, inst) == sum(t in set(inst.planted) for t in M)


# ------------------------------------------------------------------ DNA codec


def _codon_len_expected(n):
    L = 1
    while 3**L < n:
        L += 1
    return max(L, 2)


@pytest.mark.parametrize("inst", instances(), ids=lambda i: i.name)
def test_codec_lengths_motif_count_and_roundtrip(inst):
    codec = DNACodec(inst)
    n = len(inst.universe())
    assert codec.codon_len == _codon_len_expected(n)
    assert 3 ** codec.codon_len >= n
    codons = list(codec.codon_of.values())
    assert len(set(codons)) == n and all(set(c) <= set("ACG") for c in codons)
    S, _ = enumerate_S_L(inst)
    for M in S:
        seq = codec.encode_matching(M)
        seen = [set(), set(), set()]
        reuse = 0
        for t in matching_key(M):
            if any(t[i] in seen[i] for i in range(3)):
                reuse += 1
            for i in range(3):
                seen[i].add(t[i])
        assert len(seq) == 3 * codec.codon_len * inst.k + 4 * reuse
        # exactly one motif per reuse: data codons use only A/C/G so no accidental T-run can appear
        assert seq.count(CLASH_MOTIF) == reuse
        assert codec.has_clash_motif(seq) == (not pairwise_valid(M, inst.k))
        assert matching_key(codec.decode_matching(seq)) == matching_key(M)


def test_dna_mutation_hamming_distance_is_exactly_n_subs():
    inst = generate_demo()
    codec = DNACodec(inst)
    seq = codec.encode_matching(inst.planted)
    rng = random.Random(SEED)
    for n_subs in (1, 3, 7):
        mutated = codec.mutate(seq, n_subs, rng)
        assert sum(a != b for a, b in zip(seq, mutated)) == n_subs
        assert len(mutated) == len(seq)
