"""Metrics, CSV writer, and plots for the Phase 0 imprint sim."""

from __future__ import annotations

import csv
import random
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .baselines import greedy_matching, greedy_randomized_hit_rate, uniform_hit_probability
from .dna import DNACodec
from .enumerator import hard_valid, matching_key
from .instance import Instance3DM
from .sampler import SuperpositionSampler


def time_to_first_valid_samples(
    sampler: SuperpositionSampler,
    L_labels: set,
    trials: int,
    seed: int,
    max_draws: int = 10_000,
) -> tuple[float, float]:
    """Mean / median collapse draws until a valid matching (inf if none)."""
    if not L_labels:
        return float("inf"), float("inf")
    rng = np.random.default_rng(seed)
    lengths: list[int] = []
    for _ in range(trials):
        n = 0
        hit = False
        while n < max_draws:
            n += 1
            if sampler.collapse(rng) in L_labels:
                lengths.append(n)
                hit = True
                break
        if not hit:
            lengths.append(max_draws)
    arr = np.array(lengths, dtype=float)
    return float(arr.mean()), float(np.median(arr))


def time_to_first_valid_uniform(
    n_S: int,
    n_L: int,
    trials: int,
    seed: int,
    max_draws: int = 10_000,
) -> tuple[float, float]:
    if n_L == 0:
        return float("inf"), float("inf")
    p = n_L / n_S
    rng = np.random.default_rng(seed)
    lengths = []
    for _ in range(trials):
        n = 0
        while n < max_draws:
            n += 1
            if rng.random() < p:
                lengths.append(n)
                break
        else:
            lengths.append(max_draws)
    arr = np.array(lengths, dtype=float)
    return float(arr.mean()), float(np.median(arr))


def robustness_check(
    inst: Instance3DM,
    codec: DNACodec,
    S: list,
    n_per_rate: int,
    seed: int,
    rates: tuple[int, ...] = (0, 1, 2, 4),
) -> list[dict]:
    """Nucleotide substitutions vs decode success / motif integrity / V."""
    rng = random.Random(seed)
    rows = []
    sample = list(S) if len(S) <= 40 else rng.sample(S, 40)
    for n_subs in rates:
        decode_ok = 0
        identity = 0
        motif_ok = 0
        v_after = 0
        n = 0
        for M in sample:
            for _ in range(max(1, n_per_rate // max(len(sample), 1))):
                seq = codec.encode_matching(M)
                mut = codec.mutate(seq, n_subs, rng)
                n += 1
                motif_should = not hard_valid(M, inst.k)
                if n_subs == 0:
                    motif_ok += int(codec.has_clash_motif(mut) == motif_should)
                else:
                    # After mutation, only score "motif still present if original was invalid
                    # and body still decodable" — report raw motif flag vs original V.
                    motif_ok += int(codec.has_clash_motif(mut) == motif_should)
                try:
                    rec = codec.decode_matching(mut)
                    decode_ok += 1
                    if matching_key(rec) == matching_key(M):
                        identity += 1
                    if hard_valid(rec, inst.k):
                        v_after += 1
                except (ValueError, KeyError):
                    pass
        rows.append(
            {
                "n_subs": n_subs,
                "n": n,
                "decode_rate": decode_ok / n if n else 0.0,
                "identity_rate": identity / n if n else 0.0,
                "motif_agrees_orig_V": motif_ok / n if n else 0.0,
                "decoded_hard_V_rate": v_after / n if n else 0.0,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def plot_search_space(path: Path, rows: list[dict]) -> None:
    names = [r["instance"] for r in rows]
    S = [r["abs_S"] for r in rows]
    L = [r["abs_L"] for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = np.arange(len(names))
    ax.bar(x - 0.18, S, 0.36, label="|S|", color="#4c78a8")
    ax.bar(x + 0.18, L, 0.36, label="|L|", color="#54a24b")
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=25, ha="right")
    ax.set_ylabel("count")
    ax.set_title("Search-space reduction |L| / |S| (3DM imprint Phase 0)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_hit_probs(path: Path, rows: list[dict]) -> None:
    """Four bars: naive imprint | reversed imprint | uniform | randomized greedy."""
    names = [r["instance"] for r in rows]
    fig, ax = plt.subplots(figsize=(9.5, 4.4))
    x = np.arange(len(names))
    w = 0.20
    naive = [r.get("hit_imprint_naive", r.get("hit_imprint", 0.0)) for r in rows]
    rev = [r.get("hit_imprint_reversed", r.get("hit_imprint", 0.0)) for r in rows]
    ax.bar(x - 1.5 * w, naive, w, label="naive imprint (γ=0)", color="#9ecae1")
    ax.bar(x - 0.5 * w, rev, w, label="reversed imprint (γ>0)", color="#4c78a8")
    ax.bar(x + 0.5 * w, [r["hit_uniform"] for r in rows], w, label="uniform k-subset", color="#e45756")
    ax.bar(x + 1.5 * w, [r["hit_greedy_rand"] for r in rows], w, label="randomized greedy", color="#f58518")
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=25, ha="right")
    ax.set_ylabel("P(valid)")
    ax.set_ylim(0, 1.05)
    ax.set_title("Hit probability: naive | reversed | uniform | greedy")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_ttfv(path: Path, rows: list[dict]) -> None:
    names = [r["instance"] for r in rows]
    imp = [r["ttfv_imprint_mean"] for r in rows]
    uni = [r["ttfv_uniform_mean"] for r in rows]
    # inf → omit
    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = np.arange(len(names))
    ax.bar(x - 0.18, [v if np.isfinite(v) else 0 for v in imp], 0.36, label="imprint", color="#4c78a8")
    ax.bar(x + 0.18, [v if np.isfinite(v) else 0 for v in uni], 0.36, label="uniform", color="#e45756")
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=25, ha="right")
    ax.set_ylabel("draws to first valid (mean)")
    ax.set_title("Time-to-first-valid (samples, with replacement)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_robustness(path: Path, rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.2))
    xs = [r["n_subs"] for r in rows]
    ax.plot(xs, [r["decode_rate"] for r in rows], "o-", label="decode ok")
    ax.plot(xs, [r["identity_rate"] for r in rows], "s-", label="identity recovered")
    ax.plot(xs, [r["motif_agrees_orig_V"] for r in rows], "^-", label="motif agrees orig V")
    ax.set_xlabel("random nucleotide substitutions")
    ax.set_ylabel("rate")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("Encoding robustness (demo instance)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
