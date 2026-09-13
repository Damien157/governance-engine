# BioGovernance OS

**Check-only** governance channel for biology / aging / disease *intents*.

Same spine as the rest of the stack: **presence ≠ access**,
**Purpose → Cost → Risk → Authority → Audit**, govern before any side effect.

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

## Forbidden scan keys

`sequence`, `fasta`, `genome`, `oligo(s)`, `plasmid`, `primer(s)`, `primer_list`,
`synthesis_order`, `protocol`, `protocol_steps`, `gene_drive_construct`,
`select_agent_id`, `pathogen_stock` → `GOV_INTENT_INVALID`.

## Code

- `src/governed_stack/bio.py` — `GovernedBio`
- `src/governed_stack/bio_policy.py` — matrix
- `src/governed_stack/contracts.py` — `BioScanIntent` / `validate_bio_scan`
- `tests/test_bio_governance.py`
- `scripts/bio_governance_smoke.py`
