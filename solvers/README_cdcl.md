# CDCL solver (`cdcl.c`)

Exact CDCL SAT decision procedure (2WL, 1-UIP learning, VSIDS, Luby restarts).

Not part of the governance product or its pitch; kept under `solvers/` as separate engineering.

## Status

- **Hardened parser landed:** `cdcl.c` is Damien's hardened version (`parse_fail` / `parse_dimacs`),
  replacing the original file from the initial import (`fe5a678`).
- **Tests:** `solvers/tests/test_cdcl_advanced.py` (Damien's suite: two oracles, metamorphic
  relations, structured families with known counts, robustness, ASan+UBSan pass). The six
  `TestParserDefects` tests fail on the original parser and pass on the hardened one.
  The runner runs it as its own area (`scripts/run_all_works_tests.py`, `EXTRA_PYTEST_DIRS`),
  so CI runs it; the suite compiles `cdcl.c` itself with gcc (and an ASan+UBSan build).
  Without gcc it skips.
- **Stale binary:** the tracked `solvers/cdcl` binary (used by the smoke tests in
  `tests/test_math_cores.py`) is still the old build. Rebuild it with the command below, or
  add a gcc build step to CI.
- **Known limits (Damien's review):** no clause deletion and no heap for VSIDS, so it will be
  slow on hard formulas.

**Not** a P vs NP proof — exponential worst case; decides individual CNF instances.

```bash
gcc -O2 -o cdcl cdcl.c
./cdcl cnf/smoke_sat.cnf    # -> SAT
./cdcl cnf/smoke_unsat.cnf  # -> UNSAT
```

Off live `govern()` / mail / calendar path. Distinct from the Python DPLL toy in `solvers/hais_3sat.py`.
