# Haven2 empirical engine backbone

Shared dynamical core for Haven2 engines: energy recurrence, equilibrium
deviation, reset time, **transistor latch** on realm changes, EVTE score
\(C_t\), and truncated Dirichlet (zeta) spectral summaries — including a
master zeta and a grid search for optimal memory \(\rho^*\).

One-line CBF analogy: **the transistor is the discrete CBF on realm changes
(same job as \(|H|<\delta\) / \(I(S')=\mathrm{true}\)).**

These spectral summaries describe memory, agility, and scored behaviour; they
are not framed as evidence of consciousness.

## Math

### 1. Energy recurrence

\[
E_{t+1} = \rho E_t + c_t,\qquad \rho\in(0,1)
\]

\(c_t\) is engine-specific drive (volatility, weather anomaly, forecast error, …).

### 2. Equilibrium and deviation

\[
E^\*=\frac{\mathbb{E}[c]}{1-\rho}
\quad\text{(running mean of \(c\) if \(\mathbb{E}[c]\) unknown)}
\]

\[
\hat{P}_t = E_t - E^\*
\]

\(\hat{P}\) is residual influence of past shocks.

### 3. Reset time

\[
T_{\mathrm{reset}}(\varepsilon)=\left\lceil\frac{\ln(\varepsilon/|E_0-E^\*|)}{\ln\rho}\right\rceil
\]

Returns \(0\) if \(E_0=E^\*\) or already within \(\varepsilon\); rejects
\(\rho\notin(0,1)\) and \(\varepsilon\le 0\).

### 4. Transistor latch

Realm switch allowed **only** if \(|\hat{P}_t| < \varepsilon_{\mathrm{switch}}\).

- **Closed** (not met): stay in current realm even if volatility regime changes
  (blocks panic flips / whipsaw / thrashing).
- **Open** (met): \(g_{t+1}=\mathrm{TargetRealm}(V_t)\) (or engine-specific
  \(R(g_t,\mathrm{context}_t)\)).
- Otherwise \(g_{t+1}=g_t\).

### 5. Realms and EVTE score

Default toy volatility `TargetRealm`: low/mid/high vol → calm/normal/defensive.

EVTE weights (for a \(C_t\) score):

| weight | value | metric |
|--------|-------|--------|
| \(w_A\) | 0.3 | correct realm vs volatility |
| \(w_{SF}\) | 0.2 | healthy switching pattern |
| \(w_{DD}\) | 0.2 | drawdown discipline |
| \(w_{FE}\) | 0.2 | forecast calibration |
| \(w_E\) | 0.1 | energy deviation sweet spot |

\[
C_t=\mathrm{clip}\Big(\sum_i w_i\,\mathrm{metric}_i,\,0,\,1\Big)
\]

### 6. Spectral / zeta summaries (truncated Dirichlet)

\[
\check{Z}_E(\sigma)=\sum_{t=0}^{T}\frac{\hat{P}_t}{(t+1)^\sigma},\quad
Z_R(\sigma)=\sum_k\frac{1}{(\tau_k+1)^\sigma},\quad
Z_C(\sigma)=\sum_t\frac{C_t}{(t+1)^\sigma}
\]

Default \(\sigma=2\).

### 7. Master zeta / optimal memory

\[
Z_H(\sigma)=\alpha_X\|\check{Z}_E(\sigma)\|+\alpha_R Z_R(\sigma)+\alpha_C Z_C(\sigma)
\]

Combined scalar and band average:

\[
S(\rho,\sigma)=w_E E_{\mathrm{spec}}+w_R R_{\mathrm{spec}}+w_C C_{\mathrm{spec}},
\qquad
S_{\mathrm{avg}}(\rho)=\frac{1}{|\Sigma|}\sum_{\sigma\in\Sigma}S(\rho,\sigma)
\]

\[
\rho^\*=\arg\max_\rho S_{\mathrm{avg}}(\rho)
\]

with \(E_{\mathrm{spec}}=\|\check{Z}_E\|\), \(R_{\mathrm{spec}}=Z_R\),
\(C_{\mathrm{spec}}=Z_C\).

**Defaults:** \(\alpha_X=\alpha_R=\alpha_C=1\);
\(w_E=w_R=w_C=1/3\); \(\Sigma=\{1.5,2.0,2.5\}\).

## Layout

```
haven2/
  src/haven2/
    energy.py       # recurrence, E*, P̂, T_reset
    transistor.py   # latch
    realms.py       # realms + TargetRealm table
    evte.py         # C_t score
    zeta.py         # Ž_E, Z_R, Z_C, Z_H, ρ*
    engine.py       # backbone runner
  scripts/sim.py    # shock + regime latch demo + ρ* search
  tests/
  artifacts/
```

## Setup

```bash
cd /workspace/governance-engine/haven2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Tests

```bash
pytest -q
```

## Simulation

```bash
python scripts/sim.py
```

Writes:

- `artifacts/haven2_latch_sim.png` — \(E_t\), \(\hat{P}_t\), \(g_t\), transistor open/closed
- `artifacts/haven2_s_avg_rho.png` — \(S_{\mathrm{avg}}(\rho)\) and \(\rho^*\)
- `artifacts/haven2_sim_summary.json` — latch hold flag, min \(|\hat{P}|\) at blocked attempt, \(\rho^*\)
