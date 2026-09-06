"""Weighted superposition over S and collapse sampling.

H = span{|M⟩ : M in S} is implemented as a dict of amplitudes over labels.
α_M = sqrt(w(M)) / sqrt(sum w),   Pr[M] = |α_M|^2 = w(M) / sum w.

Modes
------
naive     : w(M) = α s(M) + β F(M)           (γ = 0)
reversed  : w(M) = α s(M) + β F(M) + γ g(M)  (g = greedy_prior)

Defaults (reversed): α=1.0, β=0.25, γ=100.0 — γ high enough that on YES
instances imprint hit probability approaches / matches randomized greedy,
because the prior *is* greedy's construction rule. Soft/greedy scores only
reweight; hard V still gates L.

This is a classical simulation of a weighted draw — not a quantum device.
"""

from __future__ import annotations

from typing import Callable, Literal

import numpy as np

from .baselines import greedy_prior, greedy_prior_distribution
from .enumerator import matching_key
from .instance import Instance3DM, Triple
from .verifier import SoftVerifier, fitness

Mode = Literal["naive", "reversed"]

# Documented defaults
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 0.25
DEFAULT_GAMMA = 100.0  # reversed mode; dominates soft+fitness on YES instances
DEFAULT_PEEL_MIX = 0.15


class SuperpositionSampler:
    """Weighted collapse over S.

    Parameters
    ----------
    mode : 'naive' | 'reversed'
        naive forces γ=0 (legacy soft+fitness only). reversed adds greedy_prior.
    gamma : float
        Coefficient on greedy_prior(M). Default 100.0 for reversed; forced to 0
        in naive mode.
    amp_gamma : float
        Exponent for optional amp_rounds soft-score reweighting.
    peel_mix : float
        Passed through to greedy_prior (secondary peel grade).
    """

    def __init__(
        self,
        inst: Instance3DM,
        S: list,
        soft: SoftVerifier,
        alpha: float = DEFAULT_ALPHA,
        beta: float = DEFAULT_BETA,
        mix: float = 0.5,
        amp_rounds: int = 0,
        amp_gamma: float = 1.5,
        gamma: float = DEFAULT_GAMMA,
        mode: Mode = "reversed",
        peel_mix: float = DEFAULT_PEEL_MIX,
        greedy_dist: dict | None = None,
    ):
        self.inst = inst
        self.S = S
        self.soft = soft
        self.alpha = alpha
        self.beta = beta
        self.mix = mix
        self.mode: Mode = mode
        self.gamma = 0.0 if mode == "naive" else float(gamma)
        self.peel_mix = peel_mix
        self.amp_gamma = amp_gamma
        self.labels = [matching_key(M) for M in S]
        if greedy_dist is not None:
            self._greedy_dist = greedy_dist
        elif self.gamma > 0:
            self._greedy_dist = greedy_prior_distribution(inst)
        else:
            self._greedy_dist = {}
        self.weights = self._weights()
        if amp_rounds > 0:
            self.weights = self._reweight(self.weights, amp_rounds, amp_gamma)
        self.amplitudes, self.probs = self._normalize(self.weights)

    def greedy_prior_of(self, M) -> float:
        """g(M) for diagnostics / tests."""
        if not self._greedy_dist:
            self._greedy_dist = greedy_prior_distribution(self.inst)
        return greedy_prior(
            M, self.inst, self._greedy_dist, peel_mix=self.peel_mix
        )

    def _raw_w(self, M) -> float:
        s = self.soft.soft_score(M, self.inst.k, mix=self.mix)
        f = fitness(M, self.inst)
        w = self.alpha * s + self.beta * f
        if self.gamma > 0:
            g = greedy_prior(
                M, self.inst, self._greedy_dist, peel_mix=self.peel_mix
            )
            w = w + self.gamma * g
        return max(0.0, w)

    def _weights(self) -> np.ndarray:
        w = np.array([self._raw_w(M) for M in self.S], dtype=float)
        if not np.any(w > 0):
            w = np.ones(len(self.S), dtype=float)
        return w

    def _reweight(self, w: np.ndarray, rounds: int, amp_gamma: float) -> np.ndarray:
        """Amplitude-amplification-style boost of high soft-score candidates."""
        scores = np.array(
            [self.soft.soft_score(M, self.inst.k, mix=self.mix) for M in self.S],
            dtype=float,
        )
        scores = np.clip(scores, 1e-9, None)
        out = w.copy()
        for _ in range(rounds):
            out = out * (scores**amp_gamma)
        return out

    @staticmethod
    def _normalize(w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        z = float(w.sum())
        if z <= 0:
            p = np.full(len(w), 1.0 / len(w))
        else:
            p = w / z
        amps = np.sqrt(p)
        return amps, p

    def hilbert_dict(self) -> dict[tuple[Triple, ...], complex]:
        """Classical stand-in for |ψ⟩ = Σ α_M |M⟩."""
        return {lab: complex(a, 0.0) for lab, a in zip(self.labels, self.amplitudes)}

    def collapse(self, rng: np.random.Generator) -> tuple[Triple, ...]:
        idx = int(rng.choice(len(self.labels), p=self.probs))
        return self.labels[idx]

    def sample_many(self, n: int, seed: int = 0) -> list[tuple[Triple, ...]]:
        rng = np.random.default_rng(seed)
        return [self.collapse(rng) for _ in range(n)]

    def hit_probability(self, L_labels: set[tuple[Triple, ...]]) -> float:
        return float(sum(p for lab, p in zip(self.labels, self.probs) if lab in L_labels))

    def optimal_hit_probability(
        self,
        L: list,
        score_fn: Callable | None = None,
    ) -> tuple[float, tuple[Triple, ...] | None]:
        """Pr of a max-F matching among L (0 if L empty)."""
        if not L:
            return 0.0, None
        fn = score_fn or (lambda M: fitness(M, self.inst))
        best = max(fn(M) for M in L)
        winners = {matching_key(M) for M in L if fn(M) == best}
        return (
            float(sum(p for lab, p in zip(self.labels, self.probs) if lab in winners)),
            next(iter(winners)),
        )
