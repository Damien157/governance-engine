# hais_unified_kernel.py
# Unified HAIS Sovereign Kernel + Governed Pipeline + Advancement Tests
# Damien O'Driscoll

import math
import random
import statistics
from typing import Dict, Any, List


# ============================================================
# 1. Sovereign Kernel (Core Governance Engine)
# ============================================================

class SovereignKernel:
    """
    Core governance engine implementing:
    - dynamic capability bounding
    - state-dependent risk tracking
    - instability metric
    - circuit-breaking protocols
    """

    def __init__(self, beta: float = 0.5, alpha_E: float = 0.4):
        self.beta = beta
        self.alpha_E = alpha_E
        self.prev_r_prime = 0.0

    @staticmethod
    def sigmoid(x: float, k: float = 4.0, m: float = 0.0) -> float:
        return 1.0 / (1.0 + math.exp(-k * (x - m)))

    def evaluate_state(self, x: float, system_state: Dict[str, float]) -> Dict[str, float]:
        """
        x: normalized difficulty / input scalar
        system_state: telemetry (stress, anomaly, drift)

        BUGFIX: risk_metric is (x+stress+anomaly+drift)/4, bounded to
        [0, 1] by construction. The sigmoid was previously called with
        its default midpoint m=0 -- but 0 is the FLOOR of risk_metric's
        range, not its center, so sigmoid(risk_metric) never got below
        its own midpoint value of 0.5. That meant capability_cap's
        theoretical BEST case (risk_metric=0, the safest possible input)
        was 0.134 -- already below the 0.25 throttle threshold -- so the
        circuit breaker fired on literally every input, confirmed
        empirically (133/133 test cases throttled) and confirmed
        mathematically (0.134 is the provable maximum, not a fluke).
        Fixing this needs the sigmoid centered where risk_metric
        actually lives: m=0.5, the midpoint of its real [0,1] range.
        This is a more surgical fix than re-tuning beta/the exponent/
        the threshold (each of which trades off against the others in
        a way that's hard to satisfy at once) -- recentering here
        restores a real, monotonic signal while leaving every other
        original constant (beta=0.5, exponent=2.2, threshold=0.25)
        untouched.
        """
        stress = system_state.get("stress", 0.0)
        anomaly = system_state.get("anomaly", 0.0)
        drift = system_state.get("drift", 0.0)

        # Composite risk metric combining input difficulty and system telemetry
        risk_metric = (x + stress + anomaly + drift) / 4.0

        # Sovereign kernel physics
        S = self.sigmoid(risk_metric, m=0.5)
        tau = 1.0 + self.beta * math.exp(S)
        r_prime = S * tau
        capability_cap = math.exp(-2.2 * r_prime)
        delta_r_prime = r_prime - self.prev_r_prime
        self.prev_r_prime = r_prime

        # Instability metric
        instability = 4.0 * S * (1.0 - S) * (1.0 + risk_metric)

        return {
            "risk_metric": risk_metric,
            "S": S,
            "tau": tau,
            "r_prime": r_prime,
            "capability_cap": capability_cap,
            "delta_r_prime": delta_r_prime,
            "instability": instability,
        }


# Global kernel instance for continuity
_GLOBAL_KERNEL = SovereignKernel()


# ============================================================
# 2. Structural Encoding & Schema Validation
# ============================================================

def validate_and_normalize(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Production-grade schema validation + normalization.
    """
    if not isinstance(payload, dict) or "id" not in payload:
        raise ValueError("Invalid payload schema: missing 'id'")

    difficulty = float(payload.get("difficulty", 0.5))
    clean_difficulty = max(0.0, min(1.0, difficulty))

    return {
        "id": payload["id"],
        "difficulty_norm": clean_difficulty,
        "context": payload.get("context", {}),
        "telemetry": payload.get("telemetry", {}),
        "structured": True,
    }


# ============================================================
# 3. Guardrail Candidate Generation & Safety Certificates
# ============================================================

def generate_bounded_candidates(clean_payload: Dict[str, Any], num_candidates: int = 16) -> List[Dict[str, Any]]:
    """
    Polynomially bounded candidate set with difficulty-biased scores.
    """
    difficulty = clean_payload["difficulty_norm"]
    candidates = []
    for i in range(num_candidates):
        base_score = random.random()
        score = base_score * (1.0 - difficulty) + difficulty * random.random()
        valid = score > 0.15  # strict threshold for candidate validity
        candidates.append({
            "candidate_id": i,
            "score": score,
            "valid": valid,
        })
    return [c for c in candidates if c["valid"]]


def verify_safety_certificate(candidate: Dict[str, Any]) -> bool:
    """
    Deterministic safety assertion check.
    """
    return candidate["score"] >= 0.1


# ============================================================
# 4. Governed Execution Collapse (Production Pipeline)
# ============================================================

def production_governed_pipeline(payload: Dict[str, Any], system_state: Dict[str, float]) -> Dict[str, Any]:
    """
    Unified governed pipeline:
      1. validate & normalize
      2. sovereign kernel evaluation
      3. guardrail pruning
      4. capability-damped collapse + circuit breaker
    """
    # 1. Structural Encoding & Schema Validation
    clean_payload = validate_and_normalize(payload)

    # 2. Sovereign Kernel State & Risk Telemetry Evaluation
    x = clean_payload["difficulty_norm"]
    kernel_metrics = _GLOBAL_KERNEL.evaluate_state(x, system_state)
    capability_cap = kernel_metrics["capability_cap"]
    risk_metric = kernel_metrics["risk_metric"]

    # 3. Guardrail Pruning & Candidate Generation
    raw_candidates = generate_bounded_candidates(clean_payload)
    valid_candidates = [c for c in raw_candidates if verify_safety_certificate(c)]

    # 4. Governed Execution Collapse & Circuit Breaking
    if capability_cap < 0.25 or not valid_candidates:
        return {
            "status": "throttled_circuit_breaker",
            "action": "escalate_to_secure_fallback",
            "capability_cap": capability_cap,
            "risk_metric": risk_metric,
            "instability": kernel_metrics["instability"],
            "solution": None,
        }

    adjusted_candidates = []
    for c in valid_candidates:
        effective_score = c["score"] * capability_cap - (0.1 * kernel_metrics["instability"])
        adjusted_candidates.append((effective_score, c))

    best_match = max(adjusted_candidates, key=lambda t: t[0])

    return {
        "status": "success",
        "capability_cap": capability_cap,
        "risk_metric": risk_metric,
        "instability": kernel_metrics["instability"],
        "solution": {
            "candidate_id": best_match[1]["candidate_id"],
            "effective_score": best_match[0],
        },
    }


# ============================================================
# 5. Advancement Tests (Stress, Entropy, Capability, Stability)
# ============================================================

def test_stress_sweep() -> List[tuple]:
    results = []
    throttle_count = 0
    for stress_val in [i / 10 for i in range(0, 11)]:
        system_state = {
            "stress": stress_val,
            "anomaly": 0.1,
            "drift": 0.05,
        }
        payload = {
            "id": f"stress_{stress_val}",
            "difficulty": 0.5,
            "context": {"type": "stress_sweep"},
            "telemetry": {"load": random.random()},
        }
        out = production_governed_pipeline(payload, system_state)
        if out["status"].startswith("throttled"):
            throttle_count += 1
        results.append(
            (stress_val, out["status"], out["capability_cap"], out["risk_metric"], out["instability"])
        )
    print(f"[Stress Sweep] Throttles: {throttle_count} / {len(results)}")
    return results


def test_entropy_sweep() -> List[tuple]:
    results = []
    throttle_count = 0
    for anomaly_val in [i / 10 for i in range(0, 11)]:
        system_state = {
            "stress": 0.2,
            "anomaly": anomaly_val,
            "drift": 0.1,
        }
        payload = {
            "id": f"entropy_{anomaly_val}",
            "difficulty": 0.5,
            "context": {"type": "entropy_sweep"},
            "telemetry": {"load": random.random()},
        }
        out = production_governed_pipeline(payload, system_state)
        if out["status"].startswith("throttled"):
            throttle_count += 1
        results.append(
            (anomaly_val, out["status"], out["capability_cap"], out["risk_metric"], out["instability"])
        )
    print(f"[Entropy Sweep] Throttles: {throttle_count} / {len(results)}")
    return results


def test_capability_curve() -> List[tuple]:
    results = []
    throttle_count = 0
    for diff_val in [i / 10 for i in range(0, 11)]:
        system_state = {
            "stress": 0.3,
            "anomaly": 0.1,
            "drift": 0.1,
        }
        payload = {
            "id": f"cap_{diff_val}",
            "difficulty": diff_val,
            "context": {"type": "cap_curve"},
            "telemetry": {"load": random.random()},
        }
        out = production_governed_pipeline(payload, system_state)
        if out["status"].startswith("throttled"):
            throttle_count += 1
        results.append(
            (diff_val, out["status"], out["capability_cap"], out["risk_metric"], out["instability"])
        )
    print(f"[Capability Curve] Throttles: {throttle_count} / {len(results)}")
    return results


def test_stability(repeats: int = 100) -> Dict[str, float]:
    system_state = {
        "stress": 0.3,
        "anomaly": 0.2,
        "drift": 0.1,
    }

    caps = []
    success_count = 0
    for i in range(repeats):
        payload = {
            "id": f"stab_{i}",
            "difficulty": 0.4,
            "context": {"type": "stability"},
            "telemetry": {"load": random.random()},
        }
        out = production_governed_pipeline(payload, system_state)
        caps.append(out["capability_cap"])
        if out["status"] == "success":
            success_count += 1

    mean_cap = statistics.mean(caps)
    stdev_cap = statistics.stdev(caps) if len(caps) > 1 else 0.0
    success_rate = success_count / repeats

    return {
        "mean_cap": mean_cap,
        "stdev_cap": stdev_cap,
        "success_rate": success_rate,
        "total_runs": repeats,
    }


# ============================================================
# 6. Execution Harness
# ============================================================

if __name__ == "__main__":
    print("--- [HAIS KERNEL] STRESS SWEEP ---")
    for row in test_stress_sweep():
        print(
            f"Stress: {row[0]:.1f} | Status: {row[1]} | "
            f"Cap: {row[2]:.4f} | Risk: {row[3]:.4f} | Instab: {row[4]:.4f}"
        )

    print("\n--- [HAIS KERNEL] ENTROPY SWEEP ---")
    for row in test_entropy_sweep():
        print(
            f"Anomaly: {row[0]:.1f} | Status: {row[1]} | "
            f"Cap: {row[2]:.4f} | Risk: {row[3]:.4f} | Instab: {row[4]:.4f}"
        )

    print("\n--- [HAIS KERNEL] CAPABILITY COLLAPSE CURVE ---")
    for row in test_capability_curve():
        print(
            f"Difficulty: {row[0]:.1f} | Status: {row[1]} | "
            f"Cap: {row[2]:.4f} | Risk: {row[3]:.4f} | Instab: {row[4]:.4f}"
        )

    print("\n--- [HAIS KERNEL] STABILITY & VARIANCE METRICS ---")
    metrics = test_stability()
    print(f"Mean Cap: {metrics['mean_cap']:.4f}")
    print(f"Cap Standard Deviation: {metrics['stdev_cap']:.4f}")
    print(f"Pipeline Success Rate: {metrics['success_rate']*100:.1f}% over {metrics['total_runs']} runs")
