# hais_unified_all.py
# Unified HAIS Sovereign Kernel + Full Maths + Governed Pipeline + Tests
# Damien O'Driscoll

import math
import random
import statistics
from typing import Dict, Any, List


# ============================================================
# 1. Sovereign Kernel (Full Mathematical Form)
# ============================================================

class SovereignKernel:
    """
    Sovereign governance kernel implementing:
    - S(x) = 1 / (1 + e^{-k(x - m)})
    - τ(S) = 1 + β e^S
    - r' = S * τ
    - cap = e^{-2.2 r'}
    - Δr' = r'_current - r'_previous
    - Instability I = k S (1 - S) (1 + risk_metric)
    """

    def __init__(self, beta: float = 0.5, alpha_E: float = 0.4, k_sigmoid: float = 4.0):
        self.beta = beta
        self.alpha_E = alpha_E
        self.k_sigmoid = k_sigmoid
        self.prev_r_prime = 0.0

    @staticmethod
    def sigmoid(x: float, k: float = 4.0, m: float = 0.0) -> float:
        return 1.0 / (1.0 + math.exp(-k * (x - m)))

    def evaluate_state(self, x: float, system_state: Dict[str, float]) -> Dict[str, float]:
        """
        x: normalized difficulty / input scalar
        system_state: telemetry (stress, anomaly, drift)

        risk_metric ∈ [0,1] by construction:
            risk_metric = (x + stress + anomaly + drift) / 4

        S(x) is centered at m=0.5 to match risk_metric's range.
        """
        stress = system_state.get("stress", 0.0)
        anomaly = system_state.get("anomaly", 0.0)
        drift = system_state.get("drift", 0.0)

        # Composite risk metric combining input difficulty and system telemetry
        risk_metric = (x + stress + anomaly + drift) / 4.0

        # S(x) = 1 / (1 + e^{-k(x - m)}), with m=0.5
        S = self.sigmoid(risk_metric, k=self.k_sigmoid, m=0.5)

        # τ(S) = 1 + β e^S
        tau = 1.0 + self.beta * math.exp(S)

        # r' = S * τ
        r_prime = S * tau

        # cap = e^{-2.2 r'}
        capability_cap = math.exp(-2.2 * r_prime)

        # Δr' = r'_current - r'_previous
        delta_r_prime = r_prime - self.prev_r_prime
        self.prev_r_prime = r_prime

        # Instability metric I = k S (1 - S) (1 + risk_metric)
        instability = self.k_sigmoid * S * (1.0 - S) * (1.0 + risk_metric)

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
# 4. Governed Execution Collapse (Unified Pipeline)
# ============================================================

def production_governed_pipeline(payload: Dict[str, Any], system_state: Dict[str, float]) -> Dict[str, Any]:
    """
    Unified governed pipeline:
      1. validate & normalize
      2. sovereign kernel evaluation (full maths)
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
