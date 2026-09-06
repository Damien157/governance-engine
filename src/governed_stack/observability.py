"""Decision metrics + structured logging for the live govern path (P1).

Thread-safe counters only — no Prometheus client dependency.
Sketches must not record here; only GovernedStack.govern (live gate).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

DECISIONS_LOGGER = logging.getLogger("governed_stack.decisions")


class DecisionMetrics:
    """Thread-safe allow/review/block/error counters + latency aggregates."""

    __slots__ = (
        "_lock",
        "allow",
        "review",
        "block",
        "error",
        "total",
        "latency_ms_sum",
        "latency_ms_count",
        "latency_ms_max",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.allow = 0
        self.review = 0
        self.block = 0
        self.error = 0
        self.total = 0
        self.latency_ms_sum = 0.0
        self.latency_ms_count = 0
        self.latency_ms_max = 0.0

    def record(self, decision: str, latency_ms: float) -> None:
        label = (decision or "ERROR").strip().upper()
        if label not in ("ALLOW", "REVIEW", "BLOCK", "ERROR"):
            # Map unknown / ops failures into error bucket when not a known label.
            if label in ("ERR", "FAILURE", "FAILED"):
                label = "ERROR"
            else:
                # Treat anything else as block-ish operational outcome → error
                # only when explicitly error-like; else count under block.
                label = "BLOCK" if label else "ERROR"

        with self._lock:
            self.total += 1
            if label == "ALLOW":
                self.allow += 1
            elif label == "REVIEW":
                self.review += 1
            elif label == "BLOCK":
                self.block += 1
            else:
                self.error += 1
            ms = float(latency_ms)
            if ms < 0:
                ms = 0.0
            self.latency_ms_sum += ms
            self.latency_ms_count += 1
            if ms > self.latency_ms_max:
                self.latency_ms_max = ms

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            count = self.latency_ms_count
            avg = (self.latency_ms_sum / count) if count else 0.0
            return {
                "allow": self.allow,
                "review": self.review,
                "block": self.block,
                "error": self.error,
                "total": self.total,
                "latency_ms_sum": self.latency_ms_sum,
                "latency_ms_count": count,
                "latency_ms_max": self.latency_ms_max,
                "latency_ms_avg": avg,
            }

    def reset(self) -> None:
        with self._lock:
            self.allow = 0
            self.review = 0
            self.block = 0
            self.error = 0
            self.total = 0
            self.latency_ms_sum = 0.0
            self.latency_ms_count = 0
            self.latency_ms_max = 0.0

    def prometheus_text(self, namespace: str = "governed_stack") -> str:
        """OpenMetrics / Prometheus exposition text (no external deps)."""
        snap = self.snapshot()
        ns = namespace.strip("_") or "governed_stack"
        lines = [
            f"# HELP {ns}_decisions_total Decision outcomes by label",
            f"# TYPE {ns}_decisions_total counter",
            f'{ns}_decisions_total{{decision="allow"}} {snap["allow"]}',
            f'{ns}_decisions_total{{decision="review"}} {snap["review"]}',
            f'{ns}_decisions_total{{decision="block"}} {snap["block"]}',
            f'{ns}_decisions_total{{decision="error"}} {snap["error"]}',
            f"# HELP {ns}_decisions_all_total All decisions",
            f"# TYPE {ns}_decisions_all_total counter",
            f"{ns}_decisions_all_total {snap['total']}",
            f"# HELP {ns}_decision_latency_ms_sum Cumulative decision latency (ms)",
            f"# TYPE {ns}_decision_latency_ms_sum counter",
            f"{ns}_decision_latency_ms_sum {snap['latency_ms_sum']}",
            f"# HELP {ns}_decision_latency_ms_count Decision latency samples",
            f"# TYPE {ns}_decision_latency_ms_count counter",
            f"{ns}_decision_latency_ms_count {snap['latency_ms_count']}",
            f"# HELP {ns}_decision_latency_ms_max Max decision latency (ms)",
            f"# TYPE {ns}_decision_latency_ms_max gauge",
            f"{ns}_decision_latency_ms_max {snap['latency_ms_max']}",
            "",
        ]
        return "\n".join(lines)


def structured_log(event: str, **fields: Any) -> None:
    """Emit one JSON line to logger governed_stack.decisions."""
    payload = {"event": event, "ts": time.time(), **fields}
    DECISIONS_LOGGER.info(json.dumps(payload, default=str, sort_keys=True))


def decision_label_from_envelope(envelope: Optional[Dict[str, Any]]) -> str:
    """Map a govern() envelope (or ops error) to a metrics label."""
    if not envelope:
        return "ERROR"
    if envelope.get("status") == "error":
        return "ERROR"
    decision = envelope.get("decision")
    if decision:
        return str(decision).upper()
    return "ERROR"


__all__ = [
    "DecisionMetrics",
    "structured_log",
    "decision_label_from_envelope",
    "DECISIONS_LOGGER",
]
