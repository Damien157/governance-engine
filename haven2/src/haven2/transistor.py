"""
Transistor latch — discrete CBF on realm changes.

Realm switch allowed ONLY if |Ṕ_t| < ε_switch.
Closed (not met): stay in current realm even if TargetRealm changes.
Open (met): g_{t+1} = TargetRealm(V_t) [or engine-specific R(g_t, context)].
Otherwise g_{t+1} = g_t.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from haven2.realms import Realm, TargetRealmFn, target_realm


@dataclass
class TransistorLatch:
    """Empirical latch: gate realm flips on energy residual magnitude."""

    epsilon_switch: float
    realm: Realm = Realm.NORMAL
    history_realm: list[Realm] = field(init=False, default_factory=list)
    history_open: list[bool] = field(init=False, default_factory=list)
    history_switched: list[bool] = field(init=False, default_factory=list)
    switch_times: list[int] = field(init=False, default_factory=list)
    _t: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.epsilon_switch <= 0.0:
            raise ValueError(f"epsilon_switch must be > 0, got {self.epsilon_switch}")
        self.history_realm = [self.realm]
        self.history_open = []
        self.history_switched = []
        self.switch_times = []

    def is_open(self, p_hat: float) -> bool:
        """Latch open iff |Ṕ_t| < ε_switch."""
        return abs(float(p_hat)) < self.epsilon_switch

    def step(
        self,
        p_hat: float,
        v_t: float,
        *,
        target_fn: TargetRealmFn | None = None,
        t: int | None = None,
    ) -> Realm:
        """
        Apply latch for one step.

        If open: g ← TargetRealm(V_t). If closed: g stays put.
        Records open/closed and whether a switch occurred.
        """
        open_ = self.is_open(p_hat)
        desired = (target_fn or target_realm)(v_t)
        prev = self.realm
        if open_:
            self.realm = desired
        # else: stay in current realm (closed latch)
        switched = self.realm != prev
        step_t = self._t if t is None else t
        if switched:
            self.switch_times.append(step_t)
        self.history_open.append(open_)
        self.history_switched.append(switched)
        self.history_realm.append(self.realm)
        self._t = step_t + 1
        return self.realm
