"""Hard validity gate V and a small soft/neural validity score.

Hard V always gates admission to L. Soft scores only affect superposition
weights. Invalid candidates are never admitted regardless of soft score.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from .enumerator import hard_valid, matching_key
from .instance import Instance3DM, Triple


def handcrafted_score(M: Iterable[Triple], k: int) -> float:
    """Differentiable-ish product of unique-coordinate fractions.

    Equals 1.0 iff V(M)=1 (for |M|=k). In (0,1) when there are collisions.
    """
    triples = list(M)
    if k <= 0 or len(triples) == 0:
        return 0.0
    xs = [t[0] for t in triples]
    ys = [t[1] for t in triples]
    zs = [t[2] for t in triples]
    return float(
        (len(set(xs)) / k) * (len(set(ys)) / k) * (len(set(zs)) / k)
    )


def feature_vector(M: Iterable[Triple], k: int) -> np.ndarray:
    """Six features in [0, 1]: unique fractions and collision fractions."""
    triples = list(M)
    if k <= 0:
        return np.zeros(6, dtype=float)
    xs = [t[0] for t in triples]
    ys = [t[1] for t in triples]
    zs = [t[2] for t in triples]
    ux, uy, uz = len(set(xs)), len(set(ys)), len(set(zs))
    return np.array(
        [ux / k, uy / k, uz / k, (k - ux) / k, (k - uy) / k, (k - uz) / k],
        dtype=float,
    )


class SoftVerifier:
    """Tiny logistic regression (numpy GD) estimating P(V=1 | features).

    Falls back to the handcrafted product score if untrained.
    """

    def __init__(self, lr: float = 0.4, steps: int = 400):
        self.lr = lr
        self.steps = steps
        self.w: np.ndarray | None = None  # (d+1,) bias last

    def _add_bias(self, X: np.ndarray) -> np.ndarray:
        return np.concatenate([X, np.ones((X.shape[0], 1))], axis=1)

    def train(self, matchings: list[Iterable[Triple]], k: int) -> dict:
        X = np.stack([feature_vector(M, k) for M in matchings])
        y = np.array([1.0 if hard_valid(M, k) else 0.0 for M in matchings])
        if len(np.unique(y)) < 2:
            # Degenerate label set — keep handcrafted fallback.
            self.w = None
            return {"n": int(len(y)), "pos": int(y.sum()), "acc": float("nan"), "degenerate": True}
        Phi = self._add_bias(X)
        rng = np.random.default_rng(0)
        w = rng.normal(0.0, 0.1, size=Phi.shape[1])
        for _ in range(self.steps):
            logits = Phi @ w
            p = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
            grad = Phi.T @ (p - y) / len(y)
            w = w - self.lr * grad
        self.w = w
        p = 1.0 / (1.0 + np.exp(-np.clip(Phi @ w, -30, 30)))
        acc = float(((p >= 0.5) == (y >= 0.5)).mean())
        return {"n": int(len(y)), "pos": int(y.sum()), "acc": acc, "degenerate": False}

    def train_on_instance(self, inst: Instance3DM, S: list) -> dict:
        return self.train(S, inst.k)

    def predict_proba(self, M: Iterable[Triple], k: int) -> float:
        if self.w is None:
            return handcrafted_score(M, k)
        phi = np.append(feature_vector(M, k), 1.0)
        logit = float(phi @ self.w)
        return float(1.0 / (1.0 + np.exp(-np.clip(logit, -30, 30))))

    def soft_score(self, M: Iterable[Triple], k: int, mix: float = 0.5) -> float:
        """Convex mix of trained proba and handcrafted product."""
        mix = min(1.0, max(0.0, mix))
        return mix * self.predict_proba(M, k) + (1.0 - mix) * handcrafted_score(M, k)


def admit_to_L(M: Iterable[Triple], k: int, soft: float | None = None) -> bool:
    """Hard V is the only gate. ``soft`` is ignored (weights only)."""
    _ = soft
    return hard_valid(M, k)


def fitness(M: Iterable[Triple], inst: Instance3DM) -> float:
    """Optional F: count of planted triples, else prefer smaller coordinate sums."""
    key = matching_key(M)
    if inst.planted:
        planted = set(inst.planted)
        return float(sum(1 for t in key if t in planted))
    return float(sum(3 * inst.k - (t[0] % 100 + t[1] % 100 + t[2] % 100) / 50.0 for t in key))
