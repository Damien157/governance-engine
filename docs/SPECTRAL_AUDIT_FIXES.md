# Spectral audit — silent-fallback fixes (FIX 1–4)

Honesty pass on `governed_stack.spectral_audit` and the stack zeta attach path.
Do **not** silently pretend full spectrum availability or drop zeta errors.

| Fix | Bug | Solution |
|-----|-----|----------|
| **FIX 1** | Sigma only read when top-level `haven2["sigma"]` existed (never on envelopes). | If `haven2.zeta` is a Mapping and contains `sigma`, set `out["sigma"]` from that (safe float parse). |
| **FIX 2** | Partial `Z_*` hits still set `available: True`. | Accept a tier only when **all four** of `Z_E, Z_R, Z_C, Z_H` are present. Incomplete → keep best_partial, fall through; if none complete → `available: False`, `partial: True`, clear `reason`. |
| **FIX 3** | Malformed `c` in minimal snapshot coerced to `0.0`. | Present-but-not-float-parsable → fail minimal tier (`None`). Absent/`None` → `[0.0]` ("c defaulted absent→0"). |
| **FIX 4** | `stack.py` silently `except: pass` on `zeta_summaries()`. | On Exception set `haven2_info["zeta_error"]` and append `haven2_zeta_summaries_error:…` to notes. Decision unchanged; spectrum may fall through. |

Related: [SPECTRAL_AUDIT.md](SPECTRAL_AUDIT.md), module `governed_stack.spectral_audit`.

Repeatable exercises (FIX 1–4, not happy-path curl): `scripts/spectral_fix_exercises.py`.
