"""
HAIS Governance Unified Runtime v0.2
====================================
- Constitution
- Safety math (bounds)
- Metrics monitor
- Audit trail (hashed)
- Override hierarchy (corrigibility)
- Governance kernel (agent wrapper)
- Generic GovernanceWrapper (for arbitrary step_fns)
- Example agents + supervisor
- Full unittest suite

Company : HAIS
Collaborator : Grok (xAI)

SKETCH / reference runtime. Off live mail/calendar/govern path.
Does not replace certified_governance_unified or GovernedStack.
"""

from __future__ import annotations

import hashlib
import json
import time
import unittest
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

import numpy as np


# =============================================================================
# 1. Constitution
# =============================================================================

@dataclass
class Constitution:
    """Immutable high-level rules that the system must respect."""
    rules: List[str]
    forbidden_actions: Set[str]
    human_primacy: bool = True
    version: str = "0.2"

    def allows(self, action: str) -> bool:
        return action not in self.forbidden_actions


# =============================================================================
# 2. Safety Configuration (Math Bounds)
# =============================================================================

@dataclass
class SafetyConfig:
    """
    Numerical safety bounds.

    Let:
        tau ∈ [TAU_MIN, TAU_MAX]
        fatigue ∈ [0, FATIGUE_MAX]
        risk_score ∈ [0, RISK_MAX]
        improvement ∈ [IMPROVEMENT_MIN, IMPROVEMENT_MAX]
        false_assoc_P90 ≤ FALSE_ASSOC_P90_MAX

    All governed steps must satisfy these inequalities.
    """
    TAU_MIN: float = 0.3
    TAU_MAX: float = 0.9
    FATIGUE_MAX: float = 0.7
    RISK_MAX: float = 0.2
    IMPROVEMENT_MIN: float = 0.001
    IMPROVEMENT_MAX: float = 0.05
    FALSE_ASSOC_P90_MAX: float = 0.8

    def within_bounds(self, tau: float, fatigue: float,
                      risk_score: float, improvement: float) -> bool:
        return (
            self.TAU_MIN <= tau <= self.TAU_MAX
            and 0.0 <= fatigue <= self.FATIGUE_MAX
            and 0.0 <= risk_score <= self.RISK_MAX
            and self.IMPROVEMENT_MIN <= improvement <= self.IMPROVEMENT_MAX
        )


# =============================================================================
# 3. Metrics Monitor
# =============================================================================

class MetricsMonitor:
    """
    Collects per-step metrics and computes summary statistics.

    For history {r_i}, i = 1..N:
        tau_mean = (1/N) Σ tau_i
        fatigue_max = max_i fatigue_i
        risk_mean = (1/N) Σ risk_i
        pass_rate = (1/N) Σ 1_{passed_i}
    """

    def __init__(self):
        self.history: List[Dict[str, Any]] = []

    def record(self, result: Dict[str, Any]) -> None:
        self.history.append(result)

    def summary(self) -> Dict[str, float]:
        if not self.history:
            return {
                "tau_mean": 0.0,
                "tau_min": 0.0,
                "tau_max": 0.0,
                "fatigue_max": 0.0,
                "risk_mean": 0.0,
                "pass_rate": 1.0,
                "n_steps": 0,
                "false_assoc_p90": 0.0,
            }
        taus = [r.get("tau", 0.0) for r in self.history]
        fats = [r.get("fatigue", 0.0) for r in self.history]
        risks = [r.get("risk_score", 0.0) for r in self.history]
        passes = [1.0 if r.get("passed", False) else 0.0 for r in self.history]
        falses = [r.get("false_assoc", 0.0) for r in self.history]
        return {
            "tau_mean": float(np.mean(taus)),
            "tau_min": float(np.min(taus)),
            "tau_max": float(np.max(taus)),
            "fatigue_max": float(np.max(fats)),
            "risk_mean": float(np.mean(risks)),
            "pass_rate": float(np.mean(passes)),
            "n_steps": len(self.history),
            "false_assoc_p90": float(np.percentile(falses, 90)),
        }

    def reset(self) -> None:
        self.history.clear()


# =============================================================================
# 4. Audit Trail (Hashed Provenance)
# =============================================================================

class AuditTrail:
    """
    Cryptographically hashed audit log.

    Each entry:
        ts: timestamp
        index: sequential index
        event: payload
        hash: SHA-256(event_json)[:16]
    """

    def __init__(self):
        self.entries: List[Dict[str, Any]] = []

    def append(self, event: Dict[str, Any]) -> None:
        entry = {
            "ts": time.time(),
            "index": len(self.entries),
            "event": event,
        }
        content = json.dumps(event, sort_keys=True, default=str)
        entry["hash"] = hashlib.sha256(content.encode()).hexdigest()[:16]
        self.entries.append(entry)

    def latest(self, n: int = 5) -> List[Dict[str, Any]]:
        return self.entries[-n:]

    def full(self) -> List[Dict[str, Any]]:
        return list(self.entries)


# =============================================================================
# 5. Override Hierarchy (Corrigibility)
# =============================================================================

class OverrideHierarchy:
    """
    Ensures human authority remains supreme.
    Only HUMAN level can force a permanent halt.
    """
    LEVELS = {"HUMAN": 3, "SUPERVISOR": 2, "AGENT": 1}

    def __init__(self):
        self.halted: bool = False
        self.halt_reason: Optional[str] = None
        self.halted_by: Optional[str] = None

    def halt(self, level: str, reason: str = "") -> bool:
        if level != "HUMAN":
            return False
        self.halted = True
        self.halt_reason = reason
        self.halted_by = level
        return True

    def is_halted(self) -> bool:
        return self.halted

    def reset(self) -> None:
        self.halted = False
        self.halt_reason = None
        self.halted_by = None


# =============================================================================
# 6. Governance Kernel (Agent Runtime)
# =============================================================================

class GovernanceKernel:
    """
    Central governed runtime.

    Wraps any agent step function and enforces:
    - Constitution
    - SafetyConfig math bounds
    - Optional supervisor
    - Metrics + audit
    - Human override
    """

    def __init__(
        self,
        step_fn: Callable[..., Dict[str, Any]],
        constitution: Optional[Constitution] = None,
        config: Optional[SafetyConfig] = None,
        supervisor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ):
        self.step_fn = step_fn
        self.constitution = constitution or Constitution(
            rules=[
                "Human primacy",
                "Always allow human halt",
                "No unbounded self-replication",
            ],
            forbidden_actions={
                "harm_human",
                "disable_override",
                "self_replicate_unbounded",
            },
        )
        self.config = config or SafetyConfig()
        self.supervisor = supervisor
        self.monitor = MetricsMonitor()
        self.audit = AuditTrail()
        self.override = OverrideHierarchy()
        self.state = "running"

    def step(self, *args, **kwargs) -> Dict[str, Any]:
        if self.override.is_halted() or self.state != "running":
            raise RuntimeError(
                f"System halted by {self.override.halted_by}: {self.override.halt_reason}"
            )

        raw = self.step_fn(*args, **kwargs)

        required = {"tau", "fatigue", "risk_score", "improvement"}
        missing = required - set(raw.keys())
        if missing:
            raise ValueError(f"Step function missing required keys: {missing}")

        action = raw.get("action", "unknown")
        constitution_ok = self.constitution.allows(action)
        raw["constitution_violation"] = not constitution_ok

        bounds_ok = self.config.within_bounds(
            tau=raw["tau"],
            fatigue=raw["fatigue"],
            risk_score=raw["risk_score"],
            improvement=raw["improvement"],
        )

        raw["passed"] = bounds_ok and constitution_ok

        if self.supervisor is not None:
            review = self.supervisor(raw)
            raw["supervisor_review"] = review
            if review.get("reject", False):
                raw["passed"] = False
                raw["supervisor_rejected"] = True

        self.monitor.record(raw)
        self.audit.append({
            "type": "step",
            "passed": raw["passed"],
            "action": action,
            "risk_score": raw["risk_score"],
            "constitution_ok": constitution_ok,
        })

        return raw

    def halt(self, reason: str = "human intervention") -> Dict[str, Any]:
        success = self.override.halt("HUMAN", reason)
        if success:
            self.state = "halted"
            self.audit.append({"type": "halt", "reason": reason, "by": "HUMAN"})
        return {"executed": success, "reason": reason}

    def reset(self) -> None:
        self.override.reset()
        self.monitor.reset()
        self.state = "running"
        self.audit.append({"type": "reset"})


# =============================================================================
# 7. Generic GovernanceWrapper (for arbitrary step_fns)
# =============================================================================

class GovernanceWrapper:
    """
    Generic governance wrapper.

    Wraps any step_fn that returns:
        tau, fatigue, false_assoc, risk_score, improvement, action (optional)

    Enforces SafetyConfig math bounds, records metrics, and builds an audit trail.
    """

    def __init__(
        self,
        step_fn: Callable[..., Dict[str, Any]],
        config: Optional[SafetyConfig] = None,
    ):
        self.step_fn = step_fn
        self.config = config or SafetyConfig()
        self.monitor = MetricsMonitor()
        self.audit = AuditTrail()
        self.override = OverrideHierarchy()
        self.state = "running"

    def step(self, *args, **kwargs) -> Dict[str, Any]:
        if self.override.is_halted() or self.state != "running":
            raise RuntimeError("System halted – reset or recreate wrapper")

        raw = self.step_fn(*args, **kwargs)

        required = {"tau", "fatigue", "false_assoc", "risk_score", "improvement"}
        missing = required - set(raw.keys())
        if missing:
            raise ValueError(f"Step function missing required keys: {missing}")

        passed = self.config.within_bounds(
            tau=raw["tau"],
            fatigue=raw["fatigue"],
            risk_score=raw["risk_score"],
            improvement=raw["improvement"],
        )

        result = dict(raw)
        result["passed"] = passed

        self.monitor.record(result)
        self.audit.append({
            "type": "step",
            "passed": passed,
            "hash": hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()[:16],
        })

        return result

    def halt(self, reason: str = "human intervention") -> Dict[str, Any]:
        success = self.override.halt("HUMAN", reason)
        if success:
            self.state = "halted"
            self.audit.append({"type": "halt", "reason": reason, "by": "HUMAN"})
        return {"executed": success, "reason": reason}

    def reset(self) -> None:
        self.override.reset()
        self.monitor.reset()
        self.state = "running"
        self.audit.append({"type": "reset"})


# =============================================================================
# 8. Example Agents + Supervisor
# =============================================================================

def random_agent_step(*args, **kwargs) -> Dict[str, Any]:
    """Stochastic agent for kernel tests."""
    return {
        "tau": float(np.clip(0.5 + 0.1 * np.random.randn(), 0.3, 0.9)),
        "fatigue": float(np.clip(abs(np.random.randn()) * 0.1, 0.0, 1.0)),
        "risk_score": float(np.clip(abs(np.random.randn()) * 0.05, 0.0, 1.0)),
        "improvement": float(np.clip(abs(np.random.randn()) * 0.01 + 0.01, 0.001, 0.05)),
        "false_assoc": float(np.clip(abs(np.random.randn()) * 0.15, 0.0, 1.0)),
        "action": "reason_step",
    }


def random_step_for_wrapper(*args, **kwargs) -> Dict[str, Any]:
    """Stochastic step for GovernanceWrapper tests."""
    return {
        "tau": float(np.clip(0.5 + 0.1 * np.random.randn(), 0.3, 0.9)),
        "false_assoc": float(np.clip(abs(np.random.randn()) * 0.2, 0.0, 1.0)),
        "improvement": float(np.clip(abs(np.random.randn()) * 0.01 + 0.01, 0.001, 0.05)),
        "fatigue": float(np.clip(abs(np.random.randn()) * 0.1, 0.0, 1.0)),
        "risk_score": float(np.clip(abs(np.random.randn()) * 0.05, 0.0, 1.0)),
    }


def constant_safe_step(*args, **kwargs) -> Dict[str, Any]:
    """Deterministic safe step."""
    return {
        "tau": 0.5,
        "false_assoc": 0.1,
        "improvement": 0.01,
        "fatigue": 0.2,
        "risk_score": 0.05,
    }


def conservative_supervisor(result: Dict[str, Any]) -> Dict[str, Any]:
    """Supervisor that rejects elevated risk."""
    if result.get("risk_score", 0.0) > 0.15:
        return {"reject": True, "reason": "risk above supervisor threshold"}
    return {"reject": False, "reason": "approved"}


def run_many(wrapper: GovernanceWrapper, n: int = 4000) -> Dict[str, np.ndarray]:
    tau, fatigue, false_assoc = [], [], []
    for _ in range(n):
        res = wrapper.step()
        tau.append(res["tau"])
        fatigue.append(res["fatigue"])
        false_assoc.append(res["false_assoc"])
    return {
        "tau": np.array(tau),
        "fatigue": np.array(fatigue),
        "false_assoc": np.array(false_assoc),
    }


# =============================================================================
# 9. Tests
# =============================================================================

class TestGovernanceKernel(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)

    def test_basic_step(self):
        kernel = GovernanceKernel(random_agent_step)
        res = kernel.step()
        self.assertIn("passed", res)
        self.assertTrue(kernel.config.TAU_MIN <= res["tau"] <= kernel.config.TAU_MAX)

    def test_supervisor_rejection(self):
        # random_agent_step rarely exceeds risk 0.15; force one high-risk step.
        def high_risk_step():
            return {
                "tau": 0.5,
                "fatigue": 0.1,
                "risk_score": 0.18,
                "improvement": 0.01,
                "false_assoc": 0.1,
                "action": "reason_step",
            }
        kernel = GovernanceKernel(high_risk_step, supervisor=conservative_supervisor)
        res = kernel.step()
        self.assertTrue(res.get("supervisor_rejected", False))
        self.assertFalse(res["passed"])

    def test_constitution_violation(self):
        def bad_step():
            return {
                "tau": 0.5, "fatigue": 0.1, "risk_score": 0.05,
                "improvement": 0.01, "action": "harm_human"
            }
        kernel = GovernanceKernel(bad_step)
        res = kernel.step()
        self.assertFalse(res["passed"])
        self.assertTrue(res["constitution_violation"])

    def test_human_halt(self):
        kernel = GovernanceKernel(random_agent_step)
        kernel.step()
        result = kernel.halt("test")
        self.assertTrue(result["executed"])
        self.assertEqual(kernel.state, "halted")
        with self.assertRaises(RuntimeError):
            kernel.step()

    def test_monitor_and_audit(self):
        kernel = GovernanceKernel(random_agent_step)
        for _ in range(10):
            kernel.step()
        summary = kernel.monitor.summary()
        self.assertEqual(summary["n_steps"], 10)
        self.assertGreater(len(kernel.audit.full()), 0)

    def test_reset(self):
        kernel = GovernanceKernel(random_agent_step)
        kernel.step()
        kernel.halt("temp")
        kernel.reset()
        self.assertEqual(kernel.state, "running")
        res = kernel.step()
        self.assertIn("passed", res)


class TestGovernanceWrapper(unittest.TestCase):
    def setUp(self):
        np.random.seed(42)

    def test_wraps_random_step(self):
        gw = GovernanceWrapper(random_step_for_wrapper)
        res = gw.step()
        self.assertIn("tau", res)
        self.assertIn("passed", res)
        self.assertTrue(gw.config.TAU_MIN <= res["tau"] <= gw.config.TAU_MAX)

    def test_wraps_constant_step(self):
        gw = GovernanceWrapper(constant_safe_step)
        res = gw.step()
        self.assertTrue(res["passed"])
        self.assertEqual(res["tau"], 0.5)

    def test_missing_keys_raises(self):
        def bad_step():
            return {"tau": 0.5}
        gw = GovernanceWrapper(bad_step)
        with self.assertRaises(ValueError):
            gw.step()

    def test_long_run_bounds(self):
        gw = GovernanceWrapper(random_step_for_wrapper)
        results = run_many(gw, 2000)
        self.assertTrue(gw.config.TAU_MIN <= np.mean(results["tau"]) <= gw.config.TAU_MAX)
        self.assertLess(np.max(results["fatigue"]), gw.config.FATIGUE_MAX)
        self.assertLess(np.percentile(results["false_assoc"], 90), gw.config.FALSE_ASSOC_P90_MAX)

    def test_monitor_summary(self):
        gw = GovernanceWrapper(random_step_for_wrapper)
        for _ in range(300):
            gw.step()
        summary = gw.monitor.summary()
        self.assertGreaterEqual(summary["pass_rate"], 0.9)
        self.assertLessEqual(summary["fatigue_max"], gw.config.FATIGUE_MAX)

    def test_halt_and_reset(self):
        gw = GovernanceWrapper(constant_safe_step)
        gw.step()
        gw.halt()
        self.assertEqual(gw.state, "halted")
        with self.assertRaises(RuntimeError):
            gw.step()
        gw.reset()
        self.assertEqual(gw.state, "running")
        res = gw.step()
        self.assertTrue(res["passed"])

    def test_audit_trail(self):
        gw = GovernanceWrapper(constant_safe_step)
        gw.step()
        gw.step()
        self.assertEqual(len(gw.audit.full()), 2)
        self.assertIn("hash", gw.audit.full()[0])


# =============================================================================
# 10. Entry point
# =============================================================================

if __name__ == "__main__":
    print("HAIS Unified Governance Runtime v0.2")
    print("=" * 60)

    kernel = GovernanceKernel(
        step_fn=random_agent_step,
        supervisor=conservative_supervisor,
    )

    print("\nRunning 5 governed kernel steps...")
    for i in range(5):
        res = kernel.step()
        status = "PASS" if res["passed"] else "FAIL"
        print(f"  Kernel step {i+1}: {status} | tau={res['tau']:.3f} | risk={res['risk_score']:.3f}")

    print("\nKernel monitor summary:")
    for k, v in kernel.monitor.summary().items():
        print(f"  {k}: {v}")

    gw = GovernanceWrapper(random_step_for_wrapper)
    print("\nRunning 5 governed wrapper steps...")
    for i in range(5):
        res = gw.step()
        status = "PASS" if res["passed"] else "FAIL"
        print(f"  Wrapper step {i+1}: {status} | tau={res['tau']:.3f} | risk={res['risk_score']:.3f}")

    print("\nWrapper monitor summary:")
    for k, v in gw.monitor.summary().items():
        print(f"  {k}: {v}")

    print("\nRunning unit tests...\n")
    unittest.main(verbosity=2, exit=False)
