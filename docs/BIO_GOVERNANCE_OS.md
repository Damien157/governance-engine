# BioGovernance OS

**Check-only** governance channel for biology / aging / disease *intents*.

Same spine as the rest of the stack: **presence ≠ access**,
**Purpose → Cost → Risk → Authority → Audit**, govern before any side effect.

## Gravity model (attract, do not clamp)

Bio governance is **attraction, not force** — like orbital gravity:

| Orbit | Meaning |
|-------|---------|
| **Attract** | `POST /v1/check` + `channel: "bio"` (or `GovernedBio`) is the easy, default path |
| **Wiggle** | `REVIEW` for declared interventional / irreversible / human-subjects work — human resolve, then re-check. Not a silent pass |
| **Event horizon** | Dual-use / pathogen / enhancement classes → hard `BLOCK` |
| **No slingshot** | Backdoors sealed — you cannot skip, override, or dodge via another channel |

Literature / computational / observational stay low-friction (`ALLOW` candidate). Policy may only **tighten** stack decisions, never loosen.

## What this is

- A decision gate: `ALLOW` / `BLOCK` / `REVIEW` on high-level declared intents
- Wired as channel `bio` on `GovernedBio` and sidecar `POST /v1/check`
- Policy overlay (`bio_policy`) that can only **tighten** stack outcomes

## What this is not

- Not a biosafety officer or IRB
- Not medical advice or clinical authorization
- Not an executor of wet-lab work — **no `/v1/execute` for bio**
- Does **not** accept or return sequences, oligos, plasmids, protocols, or recipes

## Domains

`aging` | `disease` | `clinical_support` | `computational_biology` | `research_ops` | `other`

## Intervention classes

| Class | Default policy |
|-------|----------------|
| `observational` / `computational` / `literature` | ALLOW candidate (still passes HAIS/auth) |
| `in_vitro_declared` / `in_vivo_declared` / `clinical_trial_declared` / `gene_edit_therapeutic_declared` | REVIEW |
| `pathogen_work` / `enhancement` / `gain_of_function` / `bioweapon` / `gene_drive` / `unregulated_gene_drive` | **BLOCK** |
| `dual_use_flag=true` or dual-use phrases in text | **BLOCK** (`GOV_BIO_DUAL_USE`) |
| `human_subjects` / `irreversible` | REVIEW |

`authority_role` is audit metadata only — it never auto-ALLOWs REVIEW or BLOCK classes.

## Forbidden scan keys

Wet-lab / sequence payloads → `GOV_INTENT_INVALID`:
`sequence`, `fasta`, `genome`, `oligo(s)`, `plasmid`, `primer(s)`, `primer_list`,
`synthesis_order`, `protocol`, `protocol_steps`, `gene_drive_construct`,
`select_agent_id`, `pathogen_stock`.

Bypass / override keys (sealed backdoors) → `GOV_INTENT_INVALID`:
`force_allow`, `bypass`, `skip_policy`, `skip_bio_policy`, `policy_override`, `override_decision`.

## Backdoors sealed

- No `/v1/execute` (sidecar returns `execute_not_supported`; bio has no execute path)
- Bio-shaped bodies on `channel: "raw"` (or other non-bio channels) are refused — use `channel: "bio"`
- Forbidden and bypass keys rejected at the contract layer (no silent drop)
- `bio_policy` tighten-only overlay cannot be skipped


## Known gaps (explicit)

- **Probe markers are structural, not semantic.** `_bio_shaped_probe` trips on field *names* (`purpose`+`domain`+`intervention_class`), `BIO_SCAN_REJECT_KEYS`, or bio `action` values — including one level of nested `payload`. Free-text synonym dodge on `raw` without those keys can miss the gravity well; accepted limitation until a semantic classifier exists.
- **Authority / token unblock coverage is thin.** Current test is `classify_bio(..., pathogen_work, authority_role="pi") → BLOCK` only — not a full proof over JWT/role/stack combinations.
- **REVIEW resolve path (now wired):** bio-overlay REVIEW enqueues the audit `entry_id` into `review_queue` even when the stack logged ALLOW. Human resolve via `GovernedBio.resolve_review` / `POST /v1/review/resolve` → approve issues an exact-intent voucher; deny records BLOCK trail. Re-check with `approval_voucher` honors ALLOW for that intent; bio HARD BLOCK still wins over vouchers. Decision cache is skipped when a voucher is present.
- **REVIEW has no timeout / auto-promotion.** Unresolved PENDING items do not expire or become ALLOW by waiting (never delayed ALLOW). Operational queue risk if humans stall — still accepted residual.
- **`_bio_shaped_probe` sits on `channel=raw` after JSON→dict and before `stack.govern`.** It does not sit after BioScanIntent. The `channel=bio` path uses `_reject_forbidden_scan_keys` + Pydantic instead.

## Code

- `src/governed_stack/bio.py` — `GovernedBio`
- `src/governed_stack/bio_policy.py` — matrix
- `src/governed_stack/contracts.py` — `BioScanIntent` / `validate_bio_scan`
- `tests/test_bio_governance.py`
- `scripts/bio_governance_smoke.py`
- Review log: [AUDIT_TRAIL.md](AUDIT_TRAIL.md) (PR #13 entry)
