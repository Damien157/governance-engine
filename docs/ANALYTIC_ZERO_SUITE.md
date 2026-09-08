# AnalyticZeroSuite — adjacent research sketch

**Status:** out-of-band research sketch. **Not** a live governance gate.
**Not** a proof of the Riemann hypothesis (or any Clay Millennium problem).

Spine links (discovery only):
[ALGORITHM_GOVERNANCE_MAIN.md](ALGORITHM_GOVERNANCE_MAIN.md),
[NVNP_CONJECTURES.md](NVNP_CONJECTURES.md).

---

## Purpose

Damien’s **AnalyticZeroSuite** groups analytic bookkeeping steps that may
someday sit *beside* the governed stack (audit / registry attribution), never
*on* the ALLOW / BLOCK decision path.

## Suite symbols (sketch vocabulary)

| Symbol | Role (sketch) |
|--------|----------------|
| **EF** | Envelope / functional form check for a candidate analytic object |
| **ZD** | Zero-detector / discrete zero-finding probe (classical numerics) |
| **ZR** | Zero-report — structured residue of ZD (locations / residuals) |
| **ACC** | Acceptance / consistency predicate over EF+ZR (local, checkable) |
| **sealToRegistry** | Append-only seal of the ACC packet into a research registry |
| **routeAttribution** | Policy hook to attribute sealed work (same spirit as AtomSafeguardII `routeReward` — **no payments**) |

None of EF / ZD / ZR / ACC are implemented as production modules in
`src/governed_stack` today. Treat names as **Hypothesis** labels.

## Relation to live stack

| Piece | Relation |
|-------|----------|
| `GovernedAlgorithm` / `GovernedStack.govern()` | **Unaffected** — AnalyticZeroSuite is not consulted for ALLOW/BLOCK |
| Audit envelope / QUANTUM line | Optional future *adjacent* seal format only; do not overload QUANTUM with RH claims |
| Imprint 3DM / Haven2 | Unrelated classical solvers; do not import AnalyticZeroSuite into them |

## Honest non-claims

- Not a Riemann proof; not a zero-free region theorem.
- Not wired into HAIS, latch, sidecar, or action bus.
- `sealToRegistry` / `routeAttribution` are documentation hooks — same
  discipline as AtomSafeguardII: attribution only, no payment rails.

## If explored later

1. Keep all code under `docs/examples/` or a clearly marked research package.
2. Gate any *run* of the suite with `GovernedAlgorithm` Purpose→Cost→Risk→Authority→Audit.
3. Never let ACC override latch / ops policy / HAIS `capability_cap`.
