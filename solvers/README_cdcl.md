# CDCL solver (`cdcl.c`)

Exact CDCL SAT decision procedure (2WL, 1-UIP learning, VSIDS, Luby restarts).

**Not** a P vs NP proof — exponential worst case; decides individual CNF instances.

```bash
gcc -O2 -o cdcl cdcl.c
./cdcl cnf/smoke_sat.cnf    # -> SAT
./cdcl cnf/smoke_unsat.cnf  # -> UNSAT
```

Off live `govern()` / mail / calendar path. Distinct from the Python DPLL toy in `solvers/hais_3sat.py`.
