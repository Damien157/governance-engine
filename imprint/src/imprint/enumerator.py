"""Exact enumerator of candidate space S and language L."""

from __future__ import annotations

from itertools import combinations
from typing import Iterable

from .instance import Instance3DM, Triple

Matching = frozenset[Triple]


def matching_key(M: Iterable[Triple]) -> tuple[Triple, ...]:
    """Canonical ordered label for a candidate (used as |M⟩ basis index)."""
    return tuple(sorted(M))


def hard_valid(M: Iterable[Triple], k: int) -> bool:
    """V(M)=1 iff M is k triples with pairwise-disjoint coordinates."""
    triples = list(M)
    if len(triples) != k or len(set(triples)) != k:
        return False
    xs = [t[0] for t in triples]
    ys = [t[1] for t in triples]
    zs = [t[2] for t in triples]
    return len(set(xs)) == k and len(set(ys)) == k and len(set(zs)) == k


def enumerate_S_L(inst: Instance3DM) -> tuple[list[Matching], list[Matching]]:
    """Return (S, L) as lists of frozensets. |S| = C(|T|, k).

    L = {M in S : V(M)=1}. Hard V is the only admission gate.
    """
    inst.validate_shape()
    S: list[Matching] = []
    L: list[Matching] = []
    for combo in combinations(inst.T, inst.k):
        M = frozenset(combo)
        S.append(M)
        if hard_valid(M, inst.k):
            L.append(M)
    return S, L
