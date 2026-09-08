"""QUANTUM line — fused continuous encoding of HAIS SovereignKernel fields.

Damien's QUANTUM audit string is a delimiter-free concatenation of ten
scalar components from the live SovereignKernel (and placeholders for
entropy-modulated thresholds not yet exposed by the kernel).

Component order (exactly 10):
  1. x     — risk_metric
  2. S     — sigmoid(risk_metric, m=0.5)
  3. τ     — tau = 1 + β·exp(S)
  4. r'    — r_prime = S·τ
  5. cap   — capability_cap = exp(-2.2·r')
  6. Δr'   — delta from previous r' (0 if unknown)
  7. T1    — entropy-modulated threshold placeholder (0 if unavailable)
  8. T2    — same
  9. T3    — same
 10. I     — instability

Encoding (reversible):
  Each float is formatted as ``{value:+015.8f}`` — sign, four integer digits,
  decimal point, eight fractional digits (15 characters). Ten fields → fixed
  total length 150. No whitespace, commas, or separators.
  Example fragment: ``+0000.50000000`` for 0.5.

This is **not** a quantum computer claim; "QUANTUM" is Damien's fused audit
label for the continuous HAIS state vector used by Algorithm Audit.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, MutableMapping, Optional, TypedDict

# Fixed encoding parameters — do not change without a schema version bump.
FIELD_WIDTH = 15
FIELD_FORMAT = "{:+015.8f}"
N_FIELDS = 10
LINE_LENGTH = FIELD_WIDTH * N_FIELDS  # 150

FIELD_KEYS = (
    "x",
    "S",
    "tau",
    "r_prime",
    "cap",
    "delta_r_prime",
    "T1",
    "T2",
    "T3",
    "I",
)

# Aliases accepted when building from a HAIS envelope / kernel dict.
_ALIASES: Dict[str, tuple[str, ...]] = {
    "x": ("x", "risk_metric", "risk"),
    "S": ("S",),
    "tau": ("tau", "τ"),
    "r_prime": ("r_prime", "r'", "r_prime"),
    "cap": ("cap", "capability_cap"),
    "delta_r_prime": ("delta_r_prime", "Δr'", "delta_r"),
    "T1": ("T1",),
    "T2": ("T2",),
    "T3": ("T3",),
    "I": ("I", "instability"),
}

# Default β matches SovereignKernel(beta=0.5).
DEFAULT_BETA = 0.5


class QuantumState(TypedDict):
    """Structured QUANTUM snapshot (Audit)."""

    x: float
    S: float
    tau: float
    r_prime: float
    cap: float
    delta_r_prime: float
    T1: float
    T2: float
    T3: float
    I: float
    # Schema honesty: T thresholds are placeholders unless supplied.
    t_thresholds_available: bool


def _pick(src: Mapping[str, Any], key: str) -> Optional[float]:
    for alias in _ALIASES[key]:
        if alias in src and src[alias] is not None:
            try:
                return float(src[alias])
            except (TypeError, ValueError):
                continue
    return None


def _reconstruct_tau(S: float, beta: float = DEFAULT_BETA) -> float:
    return 1.0 + beta * math.exp(S)


def _reconstruct_cap(r_prime: float) -> float:
    return math.exp(-2.2 * r_prime)


def build_quantum_state(
    *,
    x: Optional[float] = None,
    S: Optional[float] = None,
    tau: Optional[float] = None,
    r_prime: Optional[float] = None,
    cap: Optional[float] = None,
    delta_r_prime: Optional[float] = None,
    T1: Optional[float] = None,
    T2: Optional[float] = None,
    T3: Optional[float] = None,
    I: Optional[float] = None,
    prev_r_prime: Optional[float] = None,
    hais: Optional[Mapping[str, Any]] = None,
    kernel_metrics: Optional[Mapping[str, Any]] = None,
    beta: float = DEFAULT_BETA,
) -> QuantumState:
    """Build a typed QUANTUM state from explicit fields and/or HAIS metrics.

    Priority per field: explicit kwarg > ``kernel_metrics`` > ``hais``.
    Missing τ is reconstructed as ``1 + β·exp(S)`` when S is known.
    Missing Δr' uses ``r' - prev_r_prime`` when both known, else 0.
    T1/T2/T3 default to 0.0 and ``t_thresholds_available`` is False unless
    at least one T* was explicitly supplied (live kernel does not emit them).
    """
    src: Dict[str, Any] = {}
    if hais:
        src.update(dict(hais))
    if kernel_metrics:
        src.update(dict(kernel_metrics))

    def resolve(key: str, explicit: Optional[float]) -> Optional[float]:
        if explicit is not None:
            return float(explicit)
        return _pick(src, key)

    x_v = resolve("x", x)
    S_v = resolve("S", S)
    tau_v = resolve("tau", tau)
    r_v = resolve("r_prime", r_prime)
    cap_v = resolve("cap", cap)
    d_v = resolve("delta_r_prime", delta_r_prime)
    I_v = resolve("I", I)

    t1_explicit = T1 is not None or _pick(src, "T1") is not None
    t2_explicit = T2 is not None or _pick(src, "T2") is not None
    t3_explicit = T3 is not None or _pick(src, "T3") is not None
    T1_v = resolve("T1", T1)
    T2_v = resolve("T2", T2)
    T3_v = resolve("T3", T3)

    # Honest defaults / reconstructions (no invented physics for T*).
    if x_v is None:
        x_v = 0.0
    if S_v is None:
        # Midpoint sigmoid at risk 0.5 with m=0.5 → S=0.5 when nothing known.
        S_v = 0.5
    if tau_v is None:
        tau_v = _reconstruct_tau(S_v, beta=beta)
    if r_v is None:
        r_v = S_v * tau_v
    if cap_v is None:
        cap_v = _reconstruct_cap(r_v)
    if d_v is None:
        if prev_r_prime is not None:
            d_v = r_v - float(prev_r_prime)
        else:
            d_v = 0.0
    if I_v is None:
        I_v = 4.0 * S_v * (1.0 - S_v) * (1.0 + x_v)
    if T1_v is None:
        T1_v = 0.0
    if T2_v is None:
        T2_v = 0.0
    if T3_v is None:
        T3_v = 0.0

    return QuantumState(
        x=float(x_v),
        S=float(S_v),
        tau=float(tau_v),
        r_prime=float(r_v),
        cap=float(cap_v),
        delta_r_prime=float(d_v),
        T1=float(T1_v),
        T2=float(T2_v),
        T3=float(T3_v),
        I=float(I_v),
        t_thresholds_available=bool(t1_explicit or t2_explicit or t3_explicit),
    )


def encode_quantum_line(state: Mapping[str, Any]) -> str:
    """Encode QUANTUM state to a fixed-width fused continuous string (len=150)."""
    parts = []
    for key in FIELD_KEYS:
        if key not in state:
            raise KeyError(f"quantum state missing field: {key}")
        parts.append(FIELD_FORMAT.format(float(state[key])))
    line = "".join(parts)
    if len(line) != LINE_LENGTH:
        raise ValueError(
            f"encoded QUANTUM line length {len(line)} != expected {LINE_LENGTH}"
        )
    return line


def decode_quantum_line(s: str) -> Dict[str, float]:
    """Decode a fused QUANTUM line back to a field dict (no schema metadata)."""
    if not isinstance(s, str):
        raise TypeError("quantum_line must be str")
    if len(s) != LINE_LENGTH:
        raise ValueError(
            f"quantum_line length {len(s)} != expected {LINE_LENGTH} "
            f"({N_FIELDS}×{FIELD_WIDTH})"
        )
    out: Dict[str, float] = {}
    for i, key in enumerate(FIELD_KEYS):
        chunk = s[i * FIELD_WIDTH : (i + 1) * FIELD_WIDTH]
        try:
            out[key] = float(chunk)
        except ValueError as exc:
            raise ValueError(f"bad QUANTUM field {key!r}: {chunk!r}") from exc
    return out


def quantum_from_hais_envelope(
    hais: Optional[Mapping[str, Any]],
    *,
    prev_r_prime: Optional[float] = None,
    beta: float = DEFAULT_BETA,
) -> QuantumState:
    """Snapshot QUANTUM from a GovernedStack ``env['hais']`` dict."""
    return build_quantum_state(hais=hais or {}, prev_r_prime=prev_r_prime, beta=beta)


def attach_quantum(
    result: MutableMapping[str, Any],
    *,
    prev_r_prime: Optional[float] = None,
    beta: float = DEFAULT_BETA,
) -> MutableMapping[str, Any]:
    """Attach ``quantum`` (structured) and ``quantum_line`` (string) onto a
    GovernedAlgorithm check result (mutates and returns ``result``).
    """
    hais = result.get("hais")
    state = quantum_from_hais_envelope(hais, prev_r_prime=prev_r_prime, beta=beta)
    # Drop the boolean metadata from the line payload but keep it on structured.
    line_state = {k: state[k] for k in FIELD_KEYS}  # type: ignore[literal-required]
    result["quantum"] = dict(state)
    result["quantum_line"] = encode_quantum_line(line_state)
    return result


__all__ = [
    "FIELD_KEYS",
    "FIELD_WIDTH",
    "LINE_LENGTH",
    "N_FIELDS",
    "QuantumState",
    "attach_quantum",
    "build_quantum_state",
    "decode_quantum_line",
    "encode_quantum_line",
    "quantum_from_hais_envelope",
]
