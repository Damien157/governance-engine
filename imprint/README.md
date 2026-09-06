# Imprint pipeline Phase 0 — 3-dimensional matching

Classical **simulation** of Damien’s four-step imprint pipeline on the
NP-complete 3-dimensional matching (3DM) decision problem. Hilbert space
\(\mathcal{H}\) is a dict of amplitudes over candidate labels, not a quantum
device.

**Guardrail:** this simulation reports empirical hit rates against classical baselines and does not claim to resolve P vs NP.

## Math

**Instance.** Finite sets \(X,Y,Z\) and triples \(T\subseteq X\times Y\times Z\).

**Decision.** Exists a matching \(M\subseteq T\) with \(|M|\ge k\) such that no
two triples share any coordinate (all \(x\) distinct, all \(y\) distinct, all
\(z\) distinct). NP-complete.

**Candidate space / language.**

\[
S(X,Y,Z,T,k)=\{M\subseteq T:|M|=k\},\qquad
V(M)=1\iff M\text{ is }k\text{ triples with pairwise-disjoint coordinates},
\]

\[
L=\{M\in S:V(M)=1\}.
\]

**Hilbert space (classical stand-in).**

\[
\mathcal{H}=\mathrm{span}\{|M\rangle:M\in S\}.
\]

**Amplitudes / collapse.** Weights mix a soft validity score \(s\), optional
fitness \(F\) (planted-triple count when known), and — in **reversed** mode —
an empirical **greedy prior** \(g(M)\): the fraction of permutations of \(T\)
on which ``greedy_matching`` returns exactly \(M\) (exact for \(|T|\le 10\)),
plus a cheap peel grade (reverse of grow-until-\(k\)). Then

\[
w(M)=\alpha\,s(M)+\beta\,F(M)+\gamma\,g(M),
\qquad
\alpha_M=\sqrt{w(M)}\Big/\sqrt{\sum_{M'}w(M')},\qquad
\mathrm{Pr}[M]=|\alpha_M|^2.
\]

**Naive** mode sets \(\gamma=0\) (legacy soft+fitness). Default \(\gamma\) is
high so YES-instance reversed imprint hit probability approaches or matches
randomized greedy — because the prior *is* greedy's construction rule.
Hard \(V\) **always** gates admission to \(L\). Soft / greedy scores only
reweight the draw. Invalids are never admitted (and \(g(M)=0\) on them).
This remains an empirical simulation; it does not claim to resolve P vs NP.

## DNA map (Damien codon + clash motif)

- Each element of \(X\cup Y\cup Z\) gets a **unique codon**: a fixed-length
  word over \(\{A,C,G\}\). \(T\) is reserved.
- A triple is \(\mathrm{codon}_x+\mathrm{codon}_y+\mathrm{codon}_z\).
- A candidate \(M\) is the **ordered** (sorted) concatenation of its \(k\)
  triples.
- **Forbidden clash motif** `TTTT`: inserted immediately before the first
  triple that reuses an \(x\), \(y\), or \(z\). Valid encodings contain no
  `T`, so non-disjoint matchings are eliminated by a sequence-level scan
  (pool \(D=\{E_{\mathrm{DNA}}(M):M\in S\}\)).
- Decoder strips the motif and recovers \(M\). Hard \(V\) still gates \(L\).

Codon table for the demo instance is written to
`artifacts/codon_map.json`.

## Four steps

1. **Encode** — DNA pool \(D\) over \(S\).
2. **Soft-verify** — handcrafted product of unique-coordinate fractions plus a
   tiny numpy logistic regression; mix controlled by `mix`.
3. **Superpose + collapse** — weighted amplitudes
   \(w=\alpha s+\beta F+\gamma g\) (naive: \(\gamma=0\)), optional one-round
   soft-score reweighting (`amp_rounds`).
4. **Hard \(V\)** — only \(V(M)=1\) enters \(L\).

## Defaults

| Symbol / flag | Value | Meaning |
|---|---|---|
| demo \(n\) | 4 | \(\|X\|=\|Y\|=\|Z\|=4\), disjoint labels so \(\|X\cup Y\cup Z\|=12\) |
| demo \(\|T\|,k\) | 9, 3 | \(\|S\|=\binom{9}{3}=84\) |
| \(\alpha,\beta\) | 1.0, 0.25 | soft vs fitness mix in \(w\) |
| \(\gamma\) | 100.0 | greedy-prior coeff (reversed mode; 0 in naive) |
| mode | `reversed` | `naive` (\(\gamma=0\)) vs `reversed` (\(\gamma>0\)) |
| `mix` | 0.5 | trained proba vs handcrafted \(s\) |
| `amp_rounds` / `amp_gamma` | 1 / 1.5 | mild soft-score reweighting |
| `peel_mix` | 0.15 | secondary peel grade inside \(g\) |
| clash motif | `TTTT` | reserved T-run |
| codon alphabet | `{A,C,G}` | T reserved; GC biased toward 50% |

Generators also emit planted-yes, planted-no (pigeonhole on \(X\)), and random
instances with \(n\in\{4,5,6\}\) and enumerable \(\binom{|T|}{k}\).

## Layout

```
imprint/
  src/imprint/     instance, enumerator, dna, verifier, sampler, baselines, pipeline
  scripts/run_sim.py
  tests/
  artifacts/       metrics.csv, robustness.csv, plots, codon_map.json
  requirements.txt
```

Sibling of the CLF-CBF-QP inner loop. This package does not import or modify
`src/governance_engine`.

## Setup

```bash
cd /workspace/governance-engine/imprint
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run tests

```bash
cd /workspace/governance-engine/imprint
.venv/bin/pytest -q
```

## Run the simulation

```bash
cd /workspace/governance-engine/imprint
.venv/bin/python scripts/run_sim.py
```

Writes:

- `artifacts/metrics.csv` — \(|S|\), \(|L|\), hit probabilities, TTFV, timings
- `artifacts/robustness.csv` — nucleotide-substitution probe on the demo
- `artifacts/search_space.png`, `hit_probability.png`, `time_to_first_valid.png`, `robustness.png`
- `artifacts/codon_map.json`, `artifacts/summary.json`

Baselines: uniform random \(k\)-subset of \(T\); greedy disjoint pick (deterministic
\(T\) order and randomized permutations). Comparison table bars:
naive imprint | reversed imprint | uniform | randomized greedy.

**Reverse empirical algorithms.** Greedy's grow-until-\(k\) rule is folded into
the amplitude prior as \(g(M)\) (permutation fraction + peel). On planted-yes
instances the reversed imprint hit rate rises toward the randomized-greedy
baseline because the prior *is* that construction rule — still purely
empirical reweighting over enumerable \(S\), not a complexity claim.

## Robustness

Random nucleotide substitutions on \(E_{\mathrm{DNA}}(M)\). At 0 substitutions,
decode recovers \(M\) and the clash motif agrees with hard \(V\). Mutations
can break codon lookup (decode fail) or desynchronize the motif.
