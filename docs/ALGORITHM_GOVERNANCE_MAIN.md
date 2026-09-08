# Algorithm Governance Main — charter

Live gate for algorithm **run / deploy** decisions. Same shape as
`GovernedMail` / `GovernedCalendar` / `GovernedPost`: scan an intent, call
`GovernedStack.govern()`, return **ALLOW / BLOCK / REVIEW**. Does **not**
execute algorithms; it only decides.

This is **not** a P vs NP proof, a complexity-class certificate, or a claim
that any particular speedup is asymptotically optimal.

## Five axes

| Axis | What is scanned / enforced | Notes |
|------|----------------------------|--------|
| **Purpose** | Why the algorithm runs (purpose + short summary) | Must be explicit before run/deploy |
| **Cost** | Time, space, energy, claimed speedup | Strings or simple numbers; energy is a **Cost estimate**, not a physics meter |
| **Risk** | Risk notes + optional security margin | Security concerns live under Risk |
| **Authority** | Who may approve (token / role via the stack) | Same JWT/role path as other live gates |
| **Audit** | Signed durable entry (`entry_id`, reasons) | Same audit chain as mail/calendar/social |

## Decision vocabulary

- **ALLOW** — agent may proceed to run or deploy (outside this gate).
- **BLOCK** — refuse; do not run, deploy, or rewrite around the policy.
- **REVIEW** — escalate to a human; do not run until approved.

## Cost vs Risk (honest mapping)

- **Speedup** is recorded under **Cost** (claimed resource improvement).
- **Security** belongs under **Risk** (notes / security margin), not Cost.
- **Energy** is a Cost estimate field only — not a guarantee of joules or carbon.

## Secrets stay out

Scan intents must **not** carry credentials. Keys such as `private_key`,
`password`, `token`, and `secret` are **rejected** (no silent drop) via
`AlgorithmScanIntent` / `validate_algorithm_scan`.

## Agent entry

See `src/governed_stack/AGENT_ALGORITHM.md` and `GovernedAlgorithm` in
`src/governed_stack/algorithm.py`.

## QUANTUM Audit (HAIS fused line)

When `GovernedAlgorithm.check` / `check_sync` returns, Audit also includes:

- `quantum` — structured HAIS snapshot (`x, S, tau, r_prime, cap, delta_r_prime, T1, T2, T3, I`)
- `quantum_line` — fixed-width fused continuous string (150 chars; see encoding docs)

Details: [QUANTUM_LINE.md](QUANTUM_LINE.md). Related charters:
[GOVERNED_CONTROLLER_NN.md](GOVERNED_CONTROLLER_NN.md),
[NVNP_CONJECTURES.md](NVNP_CONJECTURES.md) (conjectures only — **not** a P vs NP proof).
