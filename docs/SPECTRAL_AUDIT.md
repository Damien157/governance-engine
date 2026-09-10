# Spectral audit — Haven2 zeta summaries on Algorithm Audit

Live instrument: attach finite Dirichlet **spectral summaries** from Haven2
alongside QUANTUM fields on `GovernedAlgorithm` check results.

## What it is

Module: `governed_stack.spectral_audit`.

After `GovernedStack.govern()`, Haven2 has stepped once (or more) on the
stack’s `Haven2Engine`. That engine can emit:

| Key | Meaning (Haven2) |
|-----|------------------|
| `Z_E` | Energy residual mass — `energy_zeta(history_p_hat, σ)` |
| `Z_R` | Realm-switch agility — `realm_switch_zeta(switch_times, σ)` |
| `Z_C` | Scored behaviour mass — `engine_zeta(c_scores, σ)` |
| `Z_H` | Master blend — `master_zeta(...)` |

Helpers: `build_spectrum`, `attach_spectrum` → `result["spectrum"]`.

Default `σ = 2.0` (same as `haven2.zeta.DEFAULT_SIGMA`).

## Honesty — read before quoting

- These are **finite Dirichlet sums** over the current engine traces.
- **Not** the Riemann zeta function, **not** a Millennium Prize claim,
  **not** quantum computing, **not** a consciousness meter.
- Algorithm gates typically see a **short** live history (often one step per
  `govern`). Full multi-step zeta needs a sim/trace via
  `Haven2Engine.zeta_summaries()`.
- Prefer real engine values; never invent fake physics. When only a residual
  `p_hat` / `c` is available, a length-1 snapshot is labelled
  `source=minimal_residual_snapshot` with an explicit note.

## Envelope + gate wiring

1. `GovernedStack.govern()` may place `haven2.zeta = engine.zeta_summaries()`
   on the envelope (after the Haven2 step).
2. `GovernedAlgorithm.check` / `check_sync` calls `attach_quantum` then
   `attach_spectrum(result, env, engine=stack.haven2)`.

Priority inside `build_spectrum`:

1. Zeta already on `env["haven2"]` (nested `zeta` or flat `Z_*`)
2. `engine.zeta_summaries()`
3. Minimal residual snapshot from `p_hat` / `c`
4. Else `available=False` with `Z_*=None` and a `reason` string

## Example keys on `check_sync`

```python
result = gate.check_sync(purpose="batch_dedupe", summary="…")
result["quantum"]       # HAIS structured snapshot
result["quantum_line"]  # fused 150-char string
result["spectrum"]      # e.g. Z_E, Z_R, Z_C, Z_H, sigma, source, notes, available
result["haven2"]        # realm / p_hat / open / e / c [/ zeta]
```

Related: [QUANTUM_LINE.md](QUANTUM_LINE.md),
[ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md),
[PRODUCT_SLICE.md](PRODUCT_SLICE.md).

## Silent-fallback fixes

See [SPECTRAL_AUDIT_FIXES.md](SPECTRAL_AUDIT_FIXES.md) (FIX 1–4: sigma, complete spectrum, malformed `c`, stack `zeta_error`).
