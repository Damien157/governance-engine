"""Four-step imprint pipeline on 3DM (classical simulation of H)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .baselines import (
    greedy_matching,
    greedy_prior,
    greedy_prior_distribution,
    greedy_randomized_hit_rate,
    peel_score,
    uniform_hit_probability,
)
from .dna import DNACodec, motif_agrees_with_hard_V
from .enumerator import enumerate_S_L, hard_valid, matching_key
from .instance import Instance3DM, catalog
from .metrics import (
    plot_hit_probs,
    plot_robustness,
    plot_search_space,
    plot_ttfv,
    robustness_check,
    time_to_first_valid_samples,
    time_to_first_valid_uniform,
    write_csv,
)
from .sampler import (
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    DEFAULT_GAMMA,
    DEFAULT_PEEL_MIX,
    SuperpositionSampler,
)
from .verifier import SoftVerifier, admit_to_L


def run_one(
    inst: Instance3DM,
    *,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    gamma: float = DEFAULT_GAMMA,
    mix: float = 0.5,
    amp_rounds: int = 1,
    peel_mix: float = DEFAULT_PEEL_MIX,
    ttfv_trials: int = 80,
    greedy_trials: int = 200,
    seed: int = 0,
) -> tuple[dict, dict]:
    t0 = time.perf_counter()
    S, L = enumerate_S_L(inst)
    codec = DNACodec(inst)
    D = codec.encode_pool(S)
    seq_kept = codec.sequence_filter_valid(D)
    L_labels = {matching_key(M) for M in L}
    # Motif filter must match L on uncorrupted encodings.
    motif_exact = set(seq_kept) == L_labels
    motif_checked = all(motif_agrees_with_hard_V(codec, M, inst.k) for M in S)

    soft = SoftVerifier()
    train_info = soft.train_on_instance(inst, S)

    # Shared greedy-prior table (exact for |T|<=10).
    gdist = greedy_prior_distribution(inst)

    sampler_naive = SuperpositionSampler(
        inst,
        S,
        soft,
        alpha=alpha,
        beta=beta,
        mix=mix,
        amp_rounds=amp_rounds,
        mode="naive",
        gamma=0.0,
        peel_mix=peel_mix,
        greedy_dist=gdist,
    )
    sampler_rev = SuperpositionSampler(
        inst,
        S,
        soft,
        alpha=alpha,
        beta=beta,
        mix=mix,
        amp_rounds=amp_rounds,
        mode="reversed",
        gamma=gamma,
        peel_mix=peel_mix,
        greedy_dist=gdist,
    )
    # Default "imprint" = reversed (empirical algorithms in the amplitude prior).
    sampler = sampler_rev

    hit_naive = sampler_naive.hit_probability(L_labels)
    hit_imprint = sampler_rev.hit_probability(L_labels)
    hit_uniform = uniform_hit_probability(len(S), len(L))
    hit_greedy_det = 1.0 if greedy_matching(inst) is not None else 0.0
    hit_greedy_rand = greedy_randomized_hit_rate(inst, greedy_trials, seed)
    opt_hit, _opt = sampler_rev.optimal_hit_probability(L)

    ttfv_i_mean, ttfv_i_med = time_to_first_valid_samples(
        sampler_rev, L_labels, ttfv_trials, seed
    )
    ttfv_n_mean, ttfv_n_med = time_to_first_valid_samples(
        sampler_naive, L_labels, ttfv_trials, seed + 2
    )
    ttfv_u_mean, ttfv_u_med = time_to_first_valid_uniform(
        len(S), len(L), ttfv_trials, seed + 1
    )

    # Wall-clock: one collapse draw vs one greedy pass vs one uniform draw
    rng_t = __import__("random").Random(seed)
    t1 = time.perf_counter()
    _ = sampler_rev.collapse(__import__("numpy").random.default_rng(seed))
    t_collapse = time.perf_counter() - t1
    t1 = time.perf_counter()
    _ = greedy_matching(inst)
    t_greedy = time.perf_counter() - t1
    t1 = time.perf_counter()
    _ = rng_t.sample(list(inst.T), inst.k)
    t_uniform = time.perf_counter() - t1

    # Soft scores never admit invalids; greedy_prior is 0 on invalids.
    leaked = 0
    prior_on_invalid = 0.0
    for M in S:
        s = soft.soft_score(M, inst.k, mix=mix)
        if admit_to_L(M, inst.k, soft=s) and not hard_valid(M, inst.k):
            leaked += 1
        if not hard_valid(M, inst.k):
            prior_on_invalid = max(
                prior_on_invalid,
                greedy_prior(M, inst, gdist, peel_mix=peel_mix),
                peel_score(M, inst),
            )

    elapsed = time.perf_counter() - t0
    return {
        "instance": inst.name,
        "n_X": len(inst.X),
        "n_Y": len(inst.Y),
        "n_Z": len(inst.Z),
        "abs_T": len(inst.T),
        "k": inst.k,
        "abs_S": len(S),
        "abs_L": len(L),
        "reduction_L_over_S": (len(L) / len(S)) if S else 0.0,
        "abs_D": len(D),
        "motif_filter_kept": len(seq_kept),
        "motif_equals_L": motif_exact,
        "motif_agrees_all_S": motif_checked,
        "hit_imprint_naive": hit_naive,
        "hit_imprint": hit_imprint,  # reversed (γ>0)
        "hit_imprint_reversed": hit_imprint,
        "hit_uniform": hit_uniform,
        "hit_greedy_det": hit_greedy_det,
        "hit_greedy_rand": hit_greedy_rand,
        "hit_optimal_F": opt_hit,
        "ttfv_imprint_mean": ttfv_i_mean,
        "ttfv_imprint_median": ttfv_i_med,
        "ttfv_naive_mean": ttfv_n_mean,
        "ttfv_naive_median": ttfv_n_med,
        "ttfv_uniform_mean": ttfv_u_mean,
        "ttfv_uniform_median": ttfv_u_med,
        "t_collapse_s": t_collapse,
        "t_greedy_s": t_greedy,
        "t_uniform_s": t_uniform,
        "soft_train_acc": train_info.get("acc"),
        "soft_train_n": train_info.get("n"),
        "soft_leaked_invalids": leaked,
        "greedy_prior_max_on_invalid": prior_on_invalid,
        "gamma": gamma,
        "alpha": alpha,
        "beta": beta,
        "elapsed_s": elapsed,
        "notes": inst.notes,
    }, {
        "inst": inst,
        "S": S,
        "L": L,
        "codec": codec,
        "D": D,
        "sampler": sampler,
        "sampler_naive": sampler_naive,
        "sampler_reversed": sampler_rev,
        "soft": soft,
        "train_info": train_info,
        "greedy_dist": gdist,
        "document_map": codec.document_map(),
    }


def run_pipeline(
    artifacts: Path,
    *,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    gamma: float = DEFAULT_GAMMA,
    seed: int = 0,
) -> dict:
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)

    instances = catalog(seed=seed)
    rows = []
    bundles = {}
    for inst in instances:
        row, bundle = run_one(
            inst, alpha=alpha, beta=beta, gamma=gamma, seed=seed
        )
        rows.append(row)
        bundles[inst.name] = bundle

    write_csv(artifacts / "metrics.csv", rows)

    demo_bundle = bundles["demo"]
    robust = robustness_check(
        demo_bundle["inst"],
        demo_bundle["codec"],
        demo_bundle["S"],
        n_per_rate=80,
        seed=seed,
    )
    write_csv(artifacts / "robustness.csv", robust)

    plot_search_space(artifacts / "search_space.png", rows)
    plot_hit_probs(artifacts / "hit_probability.png", rows)
    plot_ttfv(artifacts / "time_to_first_valid.png", rows)
    plot_robustness(artifacts / "robustness.png", robust)

    codon_path = artifacts / "codon_map.json"
    codon_path.write_text(json.dumps(demo_bundle["document_map"], indent=2) + "\n")

    summary = {
        "n_instances": len(rows),
        "demo": next(r for r in rows if r["instance"] == "demo"),
        "weight_defaults": {
            "alpha": alpha,
            "beta": beta,
            "gamma": gamma,
            "modes": {
                "naive": "w = α s + β F  (γ=0)",
                "reversed": "w = α s + β F + γ greedy_prior",
            },
            "note": (
                "γ default high so YES-instance reversed imprint hit rate "
                "approaches / matches randomized greedy (prior IS greedy)."
            ),
        },
        "artifacts": sorted(p.name for p in artifacts.iterdir()),
        "guardrail": (
            "This simulation reports empirical hit rates against classical "
            "baselines and does not claim to resolve P vs NP."
        ),
    }
    (artifacts / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n"
    )
    return {"rows": rows, "robustness": robust, "summary": summary, "bundles": bundles}
