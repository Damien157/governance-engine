"""Transistor latch: NEVER switch when |Ṕ| >= ε; does switch when open."""

from __future__ import annotations

from haven2.realms import Realm
from haven2.transistor import TransistorLatch


def test_never_switches_when_closed_even_if_target_changes():
    latch = TransistorLatch(epsilon_switch=0.05, realm=Realm.CALM)
    # |p_hat| >= ε_switch → closed
    p_hat = 0.2
    assert not latch.is_open(p_hat)
    # Target wants DEFENSIVE (high vol), but latch closed
    g = latch.step(p_hat, v_t=0.08)
    assert g == Realm.CALM
    assert latch.history_switched[-1] is False
    assert latch.history_open[-1] is False


def test_switches_when_open_and_target_differs():
    latch = TransistorLatch(epsilon_switch=0.05, realm=Realm.CALM)
    p_hat = 0.01  # |Ṕ| < ε
    assert latch.is_open(p_hat)
    g = latch.step(p_hat, v_t=0.08)  # high vol → defensive
    assert g == Realm.DEFENSIVE
    assert latch.history_switched[-1] is True
    assert latch.switch_times == [0]


def test_stays_when_open_but_target_matches():
    latch = TransistorLatch(epsilon_switch=0.05, realm=Realm.NORMAL)
    g = latch.step(0.0, v_t=0.02)  # mid → normal
    assert g == Realm.NORMAL
    assert latch.history_switched[-1] is False


def test_boundary_closed_at_equality():
    # Spec: open ONLY if |Ṕ| < ε_switch (strict)
    latch = TransistorLatch(epsilon_switch=0.05, realm=Realm.CALM)
    assert not latch.is_open(0.05)
    g = latch.step(0.05, v_t=0.08)
    assert g == Realm.CALM
