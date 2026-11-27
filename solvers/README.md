# solvers/ — separate from the governance product

Nothing in this folder is part of the governance engine's pitch or its live
decision path (`GovernedStack.govern()` → mail / calendar / social / tool /
bio). These are solver experiments and sketches, kept because some of them are
useful engineering in their own right.

| File | What it is | On the governance path? |
|------|------------|-------------------------|
| `cdcl.c`, `README_cdcl.md`, `cnf/` | Exact CDCL SAT solver in C (2WL, 1-UIP, VSIDS, Luby) and two smoke formulas | No |
| `hais_3sat.py` | Python DPLL toy | No |
| `unified_control_sketch.py` | Incomplete control sketch | No |
| `hais_osqp_clf_cbf_engine.py`, `governance_engine_osqp_qp.py` | OSQP CLF–CBF sketches | No |

## CDCL solver status (read before citing it)

- The `cdcl.c` in this repo is the **original** file. Git shows it unchanged
  since the initial import (`fe5a678`).
- A hardened version of the parser exists only in Damien's own copy of
  `cdcl.c`. It has **not** been committed here, and nothing in this repo
  exercises the hardening yet. Until it lands, the repo's `cdcl.c` still needs
  that hardened parser.
- `test_cdcl_advanced.py` (Damien's solver test file, reported as 44 tests
  including six parser tests that fail on the old file) is **not** in this repo
  yet, so CI does not cover `cdcl.c`. When it lands, it belongs beside the
  hardened `cdcl.c` (for example `solvers/tests/`) and must be listed in
  `scripts/run_all_works_tests.py` so the runner-coverage guard accepts it.
- What has been run against the repo's file: the smoke formulas
  (`cnf/smoke_sat.cnf` → SAT, `cnf/smoke_unsat.cnf` → UNSAT) and the
  C-vs-Python cross-check in `examples/unified_works.py` (check `[1b]`,
  well-formed CRLF input only). Neither says anything about the hardening.

`examples/unified_works.py` contains a Python port of the solver and is a demo,
not a test suite.
