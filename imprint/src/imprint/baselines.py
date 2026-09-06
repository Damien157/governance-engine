"""Classical baselines: uniform random k-subset and greedy disjoint pick.

Also: reverse-empirical priors that turn greedy's construction rule into
an amplitude weight (permutation fraction + peel grade).
"""

from __future__ import annotations

import random
from collections import Counter
from itertools import permutations
from typing import Sequence

from .enumerator import hard_valid, matching_key
from .instance import Instance3DM, Triple


def uniform_sample(inst: Instance3DM, rng: random.Random) -> tuple[Triple, ...]:
    """Uniform random k-subset of T (one draw from S)."""
    return matching_key(rng.sample(list(inst.T), inst.k))


def greedy_matching(
    inst: Instance3DM,
    order: Sequence[Triple] | None = None,
) -> tuple[Triple, ...] | None:
    """Pick disjoint triples in ``order`` (default: given T order) until k or fail."""
    triples = list(order) if order is not None else list(inst.T)
    chosen: list[Triple] = []
    used_x: set[int] = set()
    used_y: set[int] = set()
    used_z: set[int] = set()
    for t in triples:
        x, y, z = t
        if x in used_x or y in used_y or z in used_z:
            continue
        chosen.append(t)
        used_x.add(x)
        used_y.add(y)
        used_z.add(z)
        if len(chosen) == inst.k:
            return matching_key(chosen)
    return None


def greedy_randomized_hit_rate(inst: Instance3DM, trials: int, seed: int) -> float:
    """Fraction of random permutations of T on which greedy reaches k."""
    rng = random.Random(seed)
    hits = 0
    T = list(inst.T)
    for _ in range(trials):
        rng.shuffle(T)
        if greedy_matching(inst, order=T) is not None:
            hits += 1
    return hits / trials if trials else 0.0


def uniform_hit_probability(n_S: int, n_L: int) -> float:
    """Exact P(V=1) under uniform draw from S."""
    return (n_L / n_S) if n_S else 0.0


def first_valid_uniform_expected(n_S: int, n_L: int) -> float:
    """Expected draws with replacement until first valid (geometric)."""
    p = uniform_hit_probability(n_S, n_L)
    return (1.0 / p) if p > 0 else float("inf")


def _conflicts(t: Triple, used_x: set[int], used_y: set[int], used_z: set[int]) -> bool:
    x, y, z = t
    return x in used_x or y in used_y or z in used_z


def peel_score(M, inst: Instance3DM) -> float:
    """Reverse of greedy grow-until-k: peel while remaining a partial matching.

    Returns 0 on hard-invalids. On valids, 0.5 + 0.5 * (fraction of unused
    triples that conflict with M — the ones greedy would have skipped after
    selecting M's members). Cheap O(|T|) grade; secondary to permutation prior.
    """
    key = matching_key(M)
    if not hard_valid(key, inst.k):
        return 0.0
    Mset = set(key)
    used_x = {t[0] for t in Mset}
    used_y = {t[1] for t in Mset}
    used_z = {t[2] for t in Mset}
    # Peeling any order of a valid matching leaves a partial matching.
    unused = [t for t in inst.T if t not in Mset]
    if not unused:
        return 1.0
    skipped = sum(1 for t in unused if _conflicts(t, used_x, used_y, used_z))
    return 0.5 + 0.5 * (skipped / len(unused))


def greedy_prior_distribution(
    inst: Instance3DM,
    *,
    max_exact: int = 10,
    sample_perms: int = 50_000,
    seed: int = 0,
) -> dict[tuple[Triple, ...], float]:
    """Map matching → fraction of T-orders on which greedy returns that matching.

    Exact enumeration of permutations when |T| <= max_exact (Phase 0 instances
    have |T| <= 10). Larger |T| falls back to Monte Carlo over ``sample_perms``.
    Invalids that greedy never emits are absent (prior 0).
    """
    T = list(inst.T)
    counts: Counter[tuple[Triple, ...]] = Counter()
    n = 0
    if len(T) <= max_exact:
        for order in permutations(T):
            M = greedy_matching(inst, order=order)
            if M is not None:
                counts[M] += 1
            n += 1
    else:
        rng = random.Random(seed)
        buf = list(T)
        for _ in range(sample_perms):
            rng.shuffle(buf)
            M = greedy_matching(inst, order=buf)
            if M is not None:
                counts[M] += 1
            n += 1
    if n == 0:
        return {}
    return {m: c / n for m, c in counts.items()}


def greedy_prior(
    M,
    inst: Instance3DM,
    distribution: dict[tuple[Triple, ...], float] | None = None,
    *,
    peel_mix: float = 0.15,
) -> float:
    """Empirical reverse of greedy: permutation-fraction (+ optional peel grade).

    ``greedy_prior`` is 0 on matchings greedy never constructs (including all
    hard-invalids). Soft/greedy scores only reweight; hard V still gates L.
    """
    key = matching_key(M)
    dist = distribution if distribution is not None else greedy_prior_distribution(inst)
    p = float(dist.get(key, 0.0))
    if peel_mix <= 0:
        return p
    peel = peel_score(key, inst)
    # Mix keeps permutation fraction dominant; peel is a cheap secondary grade.
    return (1.0 - peel_mix) * p + peel_mix * (p * peel if p > 0 else 0.0)
