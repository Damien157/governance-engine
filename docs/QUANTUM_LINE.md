# QUANTUM line — Algorithm Audit encoding

Damien’s **QUANTUM** audit label is a fused continuous string of HAIS
`SovereignKernel` scalars. It is **not** a quantum computer claim.

Live kernel (`hais/hais_unified_kernel.py`, sigmoid midpoint **m=0.5**) produces
`risk_metric`, `S`, `tau`, `r_prime`, `capability_cap`, `delta_r_prime`,
`instability`. Those map onto the QUANTUM components below.

## Component order (10)

| # | Symbol | Field key | Source |
|---|--------|-----------|--------|
| 1 | x | `x` | `risk_metric` / envelope `hais.risk` |
| 2 | S | `S` | sigmoid(risk, m=0.5) |
| 3 | τ | `tau` | `1 + β·exp(S)` (β=0.5) |
| 4 | r' | `r_prime` | `S·τ` |
| 5 | cap | `cap` | `exp(-2.2·r')` |
| 6 | Δr' | `delta_r_prime` | `r' − prev_r'` (else 0) |
| 7 | T1 | `T1` | **placeholder** — not in live kernel (0) |
| 8 | T2 | `T2` | **placeholder** — not in live kernel (0) |
| 9 | T3 | `T3` | **placeholder** — not in live kernel (0) |
|10 | I | `I` | `instability` |

**T1/T2/T3:** entropy-modulated thresholds are **not** emitted by the live
SovereignKernel or Haven2 latch today. Schema keeps three slots at `0.0` with
`t_thresholds_available=False` unless a caller supplies them. Do not invent
fake physics values.

Regime (absolute-minimum list) is **out of band** of this 10-field QUANTUM
string; see Haven2 realm / latch on the govern envelope separately.

## Encoding (reversible)

Module: `governed_stack.quantum_line`.

- Each float: `{value:+015.8f}` → 15 characters (sign + 4 integer digits + `.` + 8 fractional).
- Ten fields concatenated → **fixed length 150**, no whitespace / commas / delimiters.
- Helpers: `build_quantum_state`, `encode_quantum_line`, `decode_quantum_line`.

```python
from governed_stack.quantum_line import (
    build_quantum_state,
    encode_quantum_line,
    decode_quantum_line,
)

state = build_quantum_state(hais={"risk": 0.2, "S": 0.31, "cap": 0.4, "instability": 0.8})
line = encode_quantum_line(state)          # len == 150
back = decode_quantum_line(line)           # dict of 10 floats
```

## Wire into Algorithm gate / Audit

`GovernedAlgorithm.check` / `check_sync` attaches:

- `result["quantum"]` — structured dict (+ `t_thresholds_available`)
- `result["quantum_line"]` — fused string

Built from `env["hais"]` after `GovernedStack.govern()`. The live envelope now
also carries `tau` and `delta_r_prime` alongside `cap` / `risk` / `S` /
`r_prime` / `instability`.

Axis spine: [ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md).
