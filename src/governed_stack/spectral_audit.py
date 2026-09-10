"""Haven2 spectral / zeta summaries for Algorithm Audit.

Attaches a ``spectrum`` dict (Z_E, Z_R, Z_C, Z_H, …) alongside QUANTUM fields.

Honesty (read this before claiming physics):
  - Values are **finite Dirichlet sums** over the Haven2 engine's current
    energy / latch / score traces (see ``haven2.zeta``).
  - They are **not** the Riemann zeta function, not a Millennium Prize claim,
    not quantum computing, and not a consciousness meter.
  - Algorithm gates usually see a short live history (often one step per
    ``govern``). Full multi-step zeta needs a sim/trace via
    ``Haven2Engine.zeta_summaries()``.

CHANGES / FIXES (silent-fallback honesty):
  FIX 1 — sigma preservation: if ``haven2.zeta`` is a Mapping and contains
    ``sigma``, set ``out["sigma"]`` from that (safe float parse). Do **not**
    require a top-level ``haven2["sigma"]`` key (it never exists on envelopes).
  FIX 2 — complete spectrum or don't claim available: a tier (envelope
    nested/flat, engine, minimal) is accepted only when **all four**
    SPECTRUM_KEYS are present and float-coercible. Incomplete hits keep a
    best_partial and fall through; if nothing is complete, ``available=False``
    with partial keys + ``partial=True`` and a clear ``reason``.
  FIX 3 — malformed ``c`` in minimal snapshot: if ``c`` is **present** but not
    float-parsable, fail the minimal tier (return None). If ``c`` is absent /
    None, length-1 engine score defaults to ``[0.0]`` ("c defaulted absent→0").
  FIX 4 — stack zeta attach: ``GovernedStack.govern`` must not silently swallow
    ``haven2.zeta_summaries()`` failures; set ``haven2_info["zeta_error"]`` and
    append ``haven2_zeta_summaries_error:…`` to notes (decision unchanged).
    See also docs/SPECTRAL_AUDIT_FIXES.md.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional, Sequence

# Default σ matches haven2.zeta.DEFAULT_SIGMA
DEFAULT_SIGMA = 2.0

SPECTRUM_KEYS = ("Z_E", "Z_R", "Z_C", "Z_H")

_HONESTY_NOTE = (
    "finite Dirichlet sums over Haven2 energy/latch/score traces; "
    "not Riemann zeta / not consciousness / not quantum computing"
)

_SHORT_TRACE_NOTE = (
    "short live govern history; full multi-step zeta via "
    "Haven2Engine.zeta_summaries() when sim/trace exists"
)


def _as_float_dict(src: Mapping[str, Any], keys: Sequence[str] = SPECTRUM_KEYS) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in keys:
        if k in src and src[k] is not None:
            try:
                out[k] = float(src[k])
            except (TypeError, ValueError):
                continue
    return out


def _complete(values: Optional[Mapping[str, Any]]) -> bool:
    """True iff all four SPECTRUM_KEYS are present (already float-coerced)."""
    if not values:
        return False
    return all(k in values for k in SPECTRUM_KEYS)


def _zeta_from_haven2_block(haven2: Mapping[str, Any]) -> Optional[dict[str, float]]:
    """Pull Z_* from envelope haven2 (nested ``zeta`` or flat keys)."""
    nested = haven2.get("zeta")
    if isinstance(nested, Mapping):
        got = _as_float_dict(nested)
        if got:
            return got
    flat = _as_float_dict(haven2)
    return flat or None


def _zeta_from_engine(engine: Any, sigma: float) -> Optional[dict[str, float]]:
    """Call the same Haven2Engine instance the stack stepped."""
    if engine is None:
        return None
    fn = getattr(engine, "zeta_summaries", None)
    if not callable(fn):
        return None
    try:
        raw = fn(sigma=sigma)
    except TypeError:
        try:
            raw = fn(sigma)
        except Exception:
            return None
    except Exception:
        return None
    if not isinstance(raw, Mapping):
        return None
    got = _as_float_dict(raw)
    return got or None


def _minimal_from_scalars(
    haven2: Mapping[str, Any],
    *,
    sigma: float,
) -> Optional[dict[str, float]]:
    """Honest single-point Dirichlet snapshot from residual / latch scalars.

    When only current ``p_hat`` / ``c`` exist (no engine history handle), treat
    them as length-1 series. Z_R is 0 without switch times. Document limits.

    ``c`` honesty (FIX 3): absent/None → ``[0.0]`` ("c defaulted absent→0");
    present but not float-parsable → fail this tier (return None).
    """
    try:
        from haven2.zeta import energy_zeta, engine_zeta, master_zeta, realm_switch_zeta
    except Exception:
        return None

    p_hat = haven2.get("p_hat")
    if p_hat is None:
        return None
    try:
        p = float(p_hat)
    except (TypeError, ValueError):
        return None

    c_raw = haven2.get("c")
    if c_raw is None:
        # c defaulted absent→0 for length-1 engine score
        c_trace = [0.0]
    else:
        try:
            c_trace = [float(c_raw)]
        except (TypeError, ValueError):
            # present-but-bad: fail minimal tier (same honesty as bad p_hat)
            return None

    p_trace = [p]
    switches: list[int] = []
    # Optional explicit switch times if a caller stuffed them on the block.
    st = haven2.get("switch_times")
    if isinstance(st, (list, tuple)):
        try:
            switches = [int(x) for x in st]
        except (TypeError, ValueError):
            switches = []

    return {
        "Z_E": float(energy_zeta(p_trace, sigma)),
        "Z_R": float(realm_switch_zeta(switches, sigma)),
        "Z_C": float(engine_zeta(c_trace, sigma)),
        "Z_H": float(master_zeta(p_trace, switches, c_trace, sigma)),
    }


def _preserve_sigma_from_zeta(
    out: MutableMapping[str, Any],
    haven2: Mapping[str, Any],
    fallback: float,
) -> None:
    """FIX 1: take sigma from nested haven2.zeta when present (no top-level key)."""
    zeta_block = haven2.get("zeta")
    if isinstance(zeta_block, Mapping) and "sigma" in zeta_block:
        try:
            out["sigma"] = float(zeta_block["sigma"])
        except (TypeError, ValueError):
            out["sigma"] = float(fallback)


def build_spectrum(
    env: Optional[Mapping[str, Any]] = None,
    *,
    engine: Any = None,
    sigma: float = DEFAULT_SIGMA,
) -> dict[str, Any]:
    """Build a spectrum audit dict from envelope and/or Haven2 engine.

    Priority (each tier accepted only if **complete** — all four Z_* keys):
      1. Real zeta fields already on ``env['haven2']`` (nested or flat)
      2. ``engine.zeta_summaries(sigma)`` (same instance stack used)
      3. Minimal length-1 Dirichlet snapshot from ``p_hat`` / ``c``
      4. Explicit nulls / partial + reason when nothing complete
    """
    env = env or {}
    haven2 = env.get("haven2") if isinstance(env.get("haven2"), Mapping) else {}
    if not isinstance(haven2, Mapping):
        haven2 = {}

    source: Optional[str] = None
    values: Optional[dict[str, float]] = None
    best_partial: Optional[dict[str, float]] = None
    notes: list[str] = [_HONESTY_NOTE]

    def _keep_partial(candidate: Optional[dict[str, float]]) -> None:
        nonlocal best_partial
        if not candidate:
            return
        if best_partial is None or len(candidate) > len(best_partial):
            best_partial = dict(candidate)

    # Tier 1 — envelope nested/flat
    candidate = _zeta_from_haven2_block(haven2)
    if _complete(candidate):
        values = candidate
        source = "envelope_haven2_zeta"
    else:
        _keep_partial(candidate)

    # Tier 2 — engine
    if values is None:
        candidate = _zeta_from_engine(engine, sigma)
        if _complete(candidate):
            values = candidate
            source = "haven2_engine_zeta_summaries"
        else:
            _keep_partial(candidate)

    # Tier 3 — minimal residual snapshot
    if values is None:
        candidate = _minimal_from_scalars(haven2, sigma=sigma)
        if _complete(candidate):
            values = candidate
            source = "minimal_residual_snapshot"
            notes.append(_SHORT_TRACE_NOTE)
        else:
            _keep_partial(candidate)

    out: dict[str, Any] = {
        "sigma": float(sigma),
        "source": source,
        "notes": notes,
        "available": values is not None,
    }
    _preserve_sigma_from_zeta(out, haven2, sigma)

    if values is None:
        # Incomplete / nothing: surface whatever partial keys we found.
        for k in SPECTRUM_KEYS:
            out[k] = best_partial.get(k) if best_partial else None
        out["partial"] = True
        if best_partial:
            out["partial_keys"] = sorted(best_partial.keys())
        out["reason"] = (
            "incomplete spectrum: no tier yielded all four of "
            f"{', '.join(SPECTRUM_KEYS)}; "
            + (
                f"partial={sorted(best_partial.keys())}"
                if best_partial
                else "no haven2 zeta on envelope, no Haven2Engine.zeta_summaries, "
                "and no usable p_hat/c residual for a minimal snapshot"
            )
        )
        return out

    for k in SPECTRUM_KEYS:
        out[k] = values.get(k)
    # Trace-length honesty: live algorithm gate usually has short history.
    if source in ("envelope_haven2_zeta", "haven2_engine_zeta_summaries"):
        hist_len = None
        if engine is not None:
            try:
                hist = getattr(getattr(engine, "energy", None), "history_p_hat", None)
                if hist is not None:
                    hist_len = len(hist)
            except Exception:
                hist_len = None
        if hist_len is not None:
            out["trace_len"] = int(hist_len)
            if hist_len < 8:
                notes.append(_SHORT_TRACE_NOTE)
        else:
            notes.append(_SHORT_TRACE_NOTE)
    out["notes"] = notes
    return out


def attach_spectrum(
    result: MutableMapping[str, Any],
    env: Optional[Mapping[str, Any]] = None,
    *,
    engine: Any = None,
    sigma: float = DEFAULT_SIGMA,
) -> MutableMapping[str, Any]:
    """Attach ``result['spectrum']`` from govern envelope / Haven2 engine.

    Mutates and returns ``result``. Safe when spectrum is unavailable —
    keys are present with ``None`` and an explicit ``reason``.
    """
    # Prefer explicit env; else reuse fields already copied onto the result.
    base: Mapping[str, Any]
    if env is not None:
        base = env
    else:
        base = {
            "haven2": result.get("haven2"),
            "hais": result.get("hais"),
        }
    # If caller passed engine=None but result came from a stack-backed gate,
    # they should pass engine explicitly; we do not invent one.
    result["spectrum"] = build_spectrum(base, engine=engine, sigma=sigma)
    return result


__all__ = [
    "DEFAULT_SIGMA",
    "SPECTRUM_KEYS",
    "attach_spectrum",
    "build_spectrum",
]
