# examples/

Demos and cross-checks. Not tests (the CI runner does not run them) and not
part of the governance product.

- `unified_works.py` — one-file demo: a Python CDCL solver checked against
  brute force and against `solvers/cdcl.c` (the original, unhardened file), a
  small random 3-SAT phase-transition experiment, and a mathematical model of
  the governance invariants (tighten-only, voucher never loosens BLOCK, content
  binding). With `GOV_REPO=.` the model is compared against the real
  `src/governed_stack/bio_policy.py`. The model is not the engine; the engine's
  own tests are under `tests/`. This copy keeps the module docstring but not the
  long per-function derivations from Damien's original file.

```bash
GOV_REPO=. python3 examples/unified_works.py
```
