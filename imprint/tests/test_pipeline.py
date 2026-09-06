from imprint.baselines import greedy_matching, uniform_hit_probability
from imprint.enumerator import enumerate_S_L
from imprint.instance import generate_demo
from imprint.pipeline import run_one


def test_baselines_and_run_one():
    inst = generate_demo()
    S, L = enumerate_S_L(inst)
    assert abs(uniform_hit_probability(len(S), len(L)) - len(L) / len(S)) < 1e-12
    # deterministic greedy on T order may or may not succeed; just runs
    _ = greedy_matching(inst)
    row, bundle = run_one(inst, ttfv_trials=20, greedy_trials=40, seed=0)
    assert row["abs_S"] == len(S)
    assert row["abs_L"] == len(L)
    assert row["soft_leaked_invalids"] == 0
    assert row["greedy_prior_max_on_invalid"] == 0.0
    assert row["motif_equals_L"] is True
    assert row["abs_D"] == len(S)
    assert "codons" in bundle["document_map"]
    assert row["hit_imprint_reversed"] >= row["hit_imprint_naive"] - 1e-12
    assert "sampler_naive" in bundle and "sampler_reversed" in bundle
