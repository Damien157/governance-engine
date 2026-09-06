import numpy as np

from imprint.baselines import greedy_prior, greedy_prior_distribution, peel_score
from imprint.enumerator import enumerate_S_L, hard_valid, matching_key
from imprint.instance import generate_demo, generate_planted_no, generate_planted_yes
from imprint.sampler import SuperpositionSampler
from imprint.verifier import SoftVerifier


def test_amplitudes_and_probs():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    soft = SoftVerifier()
    soft.train_on_instance(inst, S)
    samp = SuperpositionSampler(inst, S, soft, alpha=1.0, beta=0.25, mode="naive")
    assert abs(float(samp.probs.sum()) - 1.0) < 1e-12
    assert abs(float(np.sum(np.abs(samp.amplitudes) ** 2)) - 1.0) < 1e-12
    assert len(samp.hilbert_dict()) == len(S)
    L_labels = {matching_key(M) for M in L}
    p = samp.hit_probability(L_labels)
    assert 0.0 <= p <= 1.0
    # weighted toward valids relative to uniform on this planted demo
    assert p >= (len(L) / len(S)) - 1e-12
    draws = samp.sample_many(50, seed=0)
    assert all(d in set(samp.labels) for d in draws)


def test_greedy_prior_zero_on_invalids():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    dist = greedy_prior_distribution(inst)
    L_labels = {matching_key(M) for M in L}
    for M in S:
        g = greedy_prior(M, inst, dist)
        p = peel_score(M, inst)
        if not hard_valid(M, inst.k):
            assert g == 0.0
            assert p == 0.0
            assert matching_key(M) not in dist or dist[matching_key(M)] == 0.0
        else:
            # planted / reachable valids may or may not appear in dist;
            # peel is >0 on all hard-valids
            assert p > 0.0
    # every key in dist is a valid matching
    for key in dist:
        assert key in L_labels
        assert dist[key] > 0.0


def test_greedy_prior_positive_on_planted_yes():
    inst = generate_planted_yes(n=5, n_extra=7, k=3, seed=0, name="yes")
    assert inst.planted is not None
    dist = greedy_prior_distribution(inst)
    g = greedy_prior(inst.planted, inst, dist)
    assert g > 0.0, "planted yes matching must be reachable by some greedy order"
    assert peel_score(inst.planted, inst) > 0.0


def test_reversed_beats_naive_on_demo():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    soft = SoftVerifier()
    soft.train_on_instance(inst, S)
    dist = greedy_prior_distribution(inst)
    L_labels = {matching_key(M) for M in L}
    naive = SuperpositionSampler(
        inst, S, soft, mode="naive", amp_rounds=1, greedy_dist=dist
    )
    rev = SuperpositionSampler(
        inst, S, soft, mode="reversed", gamma=100.0, amp_rounds=1, greedy_dist=dist
    )
    p_naive = naive.hit_probability(L_labels)
    p_rev = rev.hit_probability(L_labels)
    assert p_rev >= p_naive - 1e-12
    # reversed should approach greedy (demo greedy_rand ≈ 1)
    assert p_rev >= 0.85


def test_planted_no_everyone_zero():
    inst = generate_planted_no(n=5, n_triples=10, k=3, seed=2, name="no")
    S, L = enumerate_S_L(inst)
    assert len(L) == 0
    soft = SoftVerifier()
    soft.train_on_instance(inst, S)
    dist = greedy_prior_distribution(inst)
    assert dist == {} or sum(dist.values()) == 0.0
    L_labels: set = set()
    for mode, gamma in (("naive", 0.0), ("reversed", 100.0)):
        samp = SuperpositionSampler(
            inst, S, soft, mode=mode, gamma=gamma, greedy_dist=dist
        )
        assert samp.hit_probability(L_labels) == 0.0
