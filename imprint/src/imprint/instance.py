"""3DM instance types and small exact-solvable generators."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import product
from typing import Iterable, Sequence

Triple = tuple[int, int, int]


@dataclass(frozen=True)
class Instance3DM:
    """Finite 3DM instance (X, Y, Z, T, k).

    X, Y, Z are stored as sorted tuples. T is a tuple of distinct triples.
    If ``disjoint_labels`` was used at generation time, X ∪ Y ∪ Z has
    |X|+|Y|+|Z| elements and each gets its own codon.
    """

    X: tuple[int, ...]
    Y: tuple[int, ...]
    Z: tuple[int, ...]
    T: tuple[Triple, ...]
    k: int
    name: str = ""
    planted: tuple[Triple, ...] | None = None
    notes: str = ""

    def universe(self) -> tuple[int, ...]:
        """Elements of X ∪ Y ∪ Z in sorted order (codon keys)."""
        return tuple(sorted(set(self.X) | set(self.Y) | set(self.Z)))

    def validate_shape(self) -> None:
        if self.k < 1:
            raise ValueError("k must be >= 1")
        if len(self.T) < self.k:
            raise ValueError(f"|T|={len(self.T)} < k={self.k}")
        xt, yt, zt = set(self.X), set(self.Y), set(self.Z)
        for x, y, z in self.T:
            if x not in xt or y not in yt or z not in zt:
                raise ValueError(f"triple {(x, y, z)} not in X×Y×Z")


def _disjoint_xyz(n: int) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Pairwise-disjoint X, Y, Z so |X ∪ Y ∪ Z| = 3n (one codon per element)."""
    X = tuple(range(n))
    Y = tuple(range(n, 2 * n))
    Z = tuple(range(2 * n, 3 * n))
    return X, Y, Z


def generate_planted_yes(
    n: int = 5,
    n_extra: int = 6,
    k: int = 3,
    seed: int = 0,
    name: str = "planted-yes",
) -> Instance3DM:
    """Plant a disjoint matching of size k, then add extra triples.

    Default labels are pairwise disjoint across X, Y, Z.
    """
    if not (1 <= k <= n):
        raise ValueError("need 1 <= k <= n")
    rng = random.Random(seed)
    X, Y, Z = _disjoint_xyz(n)
    planted = tuple((X[i], Y[i], Z[i]) for i in range(k))
    universe_xyz = list(product(X, Y, Z))
    extras_pool = [t for t in universe_xyz if t not in set(planted)]
    rng.shuffle(extras_pool)
    extras = tuple(extras_pool[:n_extra])
    T = tuple(sorted(set(planted) | set(extras)))
    return Instance3DM(
        X=X,
        Y=Y,
        Z=Z,
        T=T,
        k=k,
        name=name,
        planted=planted,
        notes=f"planted yes: constructed matching of size {k}, plus {len(extras)} extras",
    )


def generate_planted_no(
    n: int = 5,
    n_triples: int = 10,
    k: int = 3,
    seed: int = 1,
    name: str = "planted-no",
) -> Instance3DM:
    """Force max matching < k by using only k-1 distinct X-elements in T."""
    if k < 2:
        raise ValueError("planted-no needs k >= 2")
    rng = random.Random(seed)
    X, Y, Z = _disjoint_xyz(n)
    x_bot = X[: k - 1]
    pool = list(product(x_bot, Y, Z))
    rng.shuffle(pool)
    T = tuple(sorted(set(pool[: max(n_triples, k)])))
    return Instance3DM(
        X=X,
        Y=Y,
        Z=Z,
        T=T,
        k=k,
        name=name,
        planted=None,
        notes=f"planted no: only {k-1} X-elements appear in T (pigeonhole)",
    )


def generate_random(
    n: int = 5,
    n_triples: int = 10,
    k: int = 3,
    seed: int = 2,
    name: str = "random",
) -> Instance3DM:
    """Uniform random distinct triples from X×Y×Z."""
    rng = random.Random(seed)
    X, Y, Z = _disjoint_xyz(n)
    pool = list(product(X, Y, Z))
    if n_triples > len(pool):
        raise ValueError("n_triples exceeds |X×Y×Z|")
    T = tuple(sorted(rng.sample(pool, n_triples)))
    return Instance3DM(X=X, Y=Y, Z=Z, T=T, k=k, name=name, notes="uniform random T")


def generate_demo() -> Instance3DM:
    """Deterministic default used by the Phase 0 script and tests.

    n=4, |T|=9, k=3 so |S|=C(9,3)=84 is fully enumerable.
    Two known disjoint 3-matchings are planted in T (diagonal + a derangement).
    """
    n = 4
    X, Y, Z = _disjoint_xyz(n)
    planted = ((X[0], Y[0], Z[0]), (X[1], Y[1], Z[1]), (X[2], Y[2], Z[2]))
    extra_matching = ((X[0], Y[1], Z[2]), (X[1], Y[2], Z[0]), (X[2], Y[0], Z[1]))
    extras = (
        (X[0], Y[2], Z[1]),
        (X[3], Y[3], Z[3]),
        (X[1], Y[0], Z[2]),
    )
    T = tuple(sorted(set(planted) | set(extra_matching) | set(extras)))
    return Instance3DM(
        X=X,
        Y=Y,
        Z=Z,
        T=T,
        k=3,
        name="demo",
        planted=planted,
        notes="deterministic demo: n=4, |T|=9, k=3; planted diagonal plus a 3-cycle",
    )


def catalog(seed: int = 0) -> list[Instance3DM]:
    """Small suite: demo, planted-yes, planted-no, and a couple of randoms."""
    insts = [
        generate_demo(),
        generate_planted_yes(n=5, n_extra=7, k=3, seed=seed, name="yes-n5-k3"),
        generate_planted_yes(n=6, n_extra=6, k=3, seed=seed + 1, name="yes-n6-k3"),
        generate_planted_no(n=5, n_triples=10, k=3, seed=seed + 2, name="no-n5-k3"),
        generate_random(n=4, n_triples=8, k=3, seed=seed + 3, name="rand-n4-k3"),
        generate_random(n=5, n_triples=10, k=3, seed=seed + 4, name="rand-n5-k3"),
    ]
    for inst in insts:
        inst.validate_shape()
    return insts
