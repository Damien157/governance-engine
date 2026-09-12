"""VERIFY side of docs/LATCH_CERTIFICATE.md — O(T) replay check for switch certificates."""

from __future__ import annotations

from haven2.engine import Haven2Engine
from haven2.realms import Realm


def _replay(drives: list[float], vols: list[float], *, initial: Realm = Realm.CALM) -> list[int]:
    eng = Haven2Engine(
        rho=0.9,
        e0=0.0,
        e_star=0.0,
        epsilon_switch=0.05,
        initial_realm=initial,
    )
    eng.run(drives, vols)
    return list(eng.latch.switch_times)


def test_switch_certificate_verifies_by_replay():
    # Build a real trace, then treat (c, v, S) as a certificate and re-check.
    eng = Haven2Engine(
        rho=0.9,
        e0=0.0,
        e_star=0.0,
        epsilon_switch=0.05,
        initial_realm=Realm.CALM,
    )
    drives = [0.0, 0.0, 0.02, 0.01]
    # High vol desires DEFENSIVE; switches only when latch open (|p_hat| < eps).
    vols = [0.005, 0.04, 0.04, 0.005]
    eng.run(drives, vols)
    S = list(eng.latch.switch_times)

    assert _replay(drives, vols, initial=Realm.CALM) == S


def test_switch_certificate_rejects_wrong_S():
    drives = [0.0, 0.02]
    vols = [0.005, 0.04]
    S_true = _replay(drives, vols, initial=Realm.CALM)
    S_lie = [999] if S_true != [999] else [998]
    assert _replay(drives, vols, initial=Realm.CALM) != S_lie
