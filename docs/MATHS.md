# Mathematical foundations

This document derives the models behind the Monte Carlo crush forecaster, the
evacuation-time estimator, and the privacy de-identification guarantees.

## 1. State definitions

Let:

- $Z$ = set of stand zones (19 at M. Chinnaswamy)
- $G$ = set of perimeter gates (18)
- $c_z$ = capacity of zone $z \in Z$
- $o_z$ = current occupants of zone $z$
- $d_z = 100 \cdot o_z / c_z$ = density % of zone $z$ (rounded to integer)
- $\tau_g$ = declared throughput of gate $g$ (people/min)
- $\mathbb{1}_g$ = indicator that gate $g$ is currently open

Baseline state is seeded from attendance and a type-aware fill bias:

$$
o_z = c_z \cdot \min\bigl(0.99,\ \frac{A}{\sum_{z'} c_{z'}} \cdot \beta_{\text{type}(z)} \cdot \beta_z\bigr)
$$

where $A$ is total attendance (from `match_state.json`), $\beta_{\text{type}}$
is the per-type fill bias ($0.95$ for general stands, $0.65$ for premium), and
$\beta_z$ is a per-zone realism tweak (e.g. `A_STAND` general gets $1.02$,
`P_CORPORATE` corporate gets $0.55$).

## 2. Crush definition

A **crush event in a zone** is defined as

$$
d_z \ge 95\%
$$

This 95% threshold matches the figure used in stadium-safety literature where
shoulder-to-shoulder density transitions to forced-contact crush risk
(Helbing & Mukerji, 2012; Still, 2014). It is **per-zone**, not global —
the entire stadium can be at 80% average while one corner is in crush.

A **trial-level crush event** is $\exists z \in Z : d_z \ge 95\%$.

## 3. Monte Carlo sampling model

At each of $N$ trials (default $N=200$), we sample:

$$
\tilde d_z = \mathrm{clip}_{[0,100]}\bigl(\mathrm{round}(d_z + \epsilon_z)\bigr),\quad \epsilon_z \sim \mathcal{N}(0,\ \sigma_d^2)
$$

$$
\tilde\tau_g = \max\!\bigl(0,\ \tau_g \cdot (1 + \epsilon_g)\bigr),\quad \epsilon_g \sim \mathcal{N}(0,\ \sigma_\tau^2)
$$

where $\sigma_d$ and $\sigma_\tau$ are scaled by the **threat-intel risk
level** $r \in \{\text{low}, \text{medium}, \text{high}\}$:

| Risk $r$ | $\sigma_d$ (density %) | $\sigma_\tau$ (throughput frac) |
|:---:|:---:|:---:|
| low    | 4  | 0.08 |
| medium | 8  | 0.18 |
| high   | 14 | 0.32 |

Uncertainty widens with risk — when Threat Intel flags a transit strike or
storm, the variance in arrival/exit patterns goes up, and the MC distribution
reflects that automatically.

## 4. Estimated quantities

**P(crush)** — probability that *some* zone enters crush during the trial:

$$
\hat P_{\text{crush}} = \frac{1}{N} \sum_{i=1}^{N} \mathbb{1}\bigl[\exists z : \tilde d_z^{(i)} \ge 95\bigr]
$$

**Evacuation time** per trial (simple flow-conservation model):

$$
T_{\text{evac}}^{(i)} = \frac{\mu \cdot \sum_z o_z^{(i)}}{\sum_{g : \mathbb{1}_g} \tilde\tau_g^{(i)}}\quad \text{minutes}
$$

where $\mu$ = `exit_demand_multiplier` (1.0 baseline; 1.8 during `match_end`
perturbation to model that the entire crowd is heading for exits at once
rather than gradual departure).

**P(slow evac)** — probability total evac exceeds 10 minutes:

$$
\hat P_{\text{slow}} = \frac{1}{N} \sum_{i=1}^{N} \mathbb{1}\bigl[T_{\text{evac}}^{(i)} > 10\bigr]
$$

**Per-zone percentile densities** (the 3D twin colours and the heatmap intensity
are driven by $\tilde d^{50}_z$):

$$
\tilde d^{(p)}_z = Q_p\bigl(\{\tilde d_z^{(i)}\}_{i=1}^{N}\bigr)\quad \text{for } p \in \{5, 50, 95\}
$$

**Per-zone crush probability** — how often each zone individually crossed 95%:

$$
\hat P_{\text{crush},z} = \frac{1}{N} \sum_{i=1}^{N} \mathbb{1}\bigl[\tilde d_z^{(i)} \ge 95\bigr]
$$

Top-5 by this metric drive the "Highest-risk areas" sidebar tile.

## 5. Convergence and trial count

For a Bernoulli quantity like $\hat P_{\text{crush}}$, the standard error is

$$
\mathrm{SE} = \sqrt{\frac{p(1-p)}{N}}
$$

At $N=200$ trials and $p=0.5$ (worst case), $\mathrm{SE} \approx 0.035$ — i.e.
$\pm$3.5 percentage points 68% of the time, $\pm$7pp 95% of the time. At $p=0.1$
or $p=0.9$ (the typical regime), SE drops to ≈0.021. This is **acceptable for
operator triage** (the operator sees a colour band, not a fragile decimal),
and a single trial costs ~0.1 ms so we can bump to $N=500$ if needed without
breaking the 5s tick budget.

## 6. Perturbation algebra

Each What-If scenario applies a deterministic transform $\Phi$ to baseline
state $s$ before Monte Carlo runs over $\Phi(s)$:

| Scenario | Transform |
|---|---|
| `close_gate(g)` | $\mathbb{1}_g \leftarrow 0$;  for each sibling $g' \ne g$ on the same side: $\tau_{g'} \leftarrow \tau_{g'} + 0.7 \cdot \tau_g / \|\text{siblings}\|$ |
| `open_gate(g)` | $\mathbb{1}_g \leftarrow 1$ |
| `weather_rain` | for $z \notin C$ (covered set): $o_z \leftarrow 0.75 \cdot o_z$; redistribute $\Delta = 0.25 \cdot \sum_{z \notin C} o_z$ equally to $z \in C$ |
| `match_end` | $\mu \leftarrow 1.8$ |
| `wicket_end_innings` | for $z \in \text{amenity\_zones}$: $o_z \leftarrow 1.3 \cdot o_z$ |
| `incident_zone(z)` | $o_z \leftarrow 1.15 \cdot o_z$; `panic_factor` flag set |

The 30% loss factor in `close_gate` (0.7 recovery) models congestion at sibling
gates — closing a 120/min gate doesn't simply hand 120/min to its neighbours.

## 7. Privacy de-identification — the $k$-anonymity argument

The `tools/privacy.py` post-event report applies four transforms:

1. **Direct identifier suppression**: emails, phones, names, free-text PII
   patterns are removed via regex + field-name allowlist.
2. **Quasi-identifier generalisation**: exact zone $z$ → zone family
   $\text{family}(z)$ (e.g. `A_STAND_SEC_7` → `A`). There are 8 distinct
   families.
3. **Temporal generalisation**: timestamps bucketed to nearest hour or day.
4. **Date shift**: a uniform $\Delta \sim \mathrm{Uniform}\{-30, ..., +30\}$ days
   applied identically to every record in the report.

**Claim (informal $k$-anonymity-equivalent guarantee).** Given an
attacker who knows an attendee's zone family, hour-of-day, and a coarse
incident-type, the smallest equivalence class in the released report has
size $k \ge ?$. We do not enforce a minimum $k$ at suppression time (this
is a lite version), so the true claim is:

> Re-identification of an individual attendee is not reasonably likely
> given the released report alone, but combination with an external dataset
> (e.g. ticket purchases) could reduce $k$ in rare-event categories.

A production version would compute $k$ per bucket and suppress or merge
buckets where $k < k_{\min}$ (typically $k_{\min}=5$ or $10$). The current
implementation prepares the structure but doesn't gate-keep — this is the
*lite* trade-off and is documented in `tools/privacy.py:11`.

## 8. Game theory — fan reports point system

For category $c$ with base points $p_c$, verification status $v \in \{0,1\}$:

$$
\text{points}(c, v) = p_c \cdot (1 + v)
$$

Badge tiers are piecewise on cumulative points:

$$
\text{badge}(P) = \max\{\text{name} : (\text{threshold}, \text{name}) \in B,\ P \ge \text{threshold}\}
$$

with $B = \{(0, \text{Newcomer}), (25, \text{Spotter}), (75, \text{Sentinel}), (200, \text{Veteran}), (500, \text{Stadium Guardian})\}$.

The 2× multiplier for volunteer-verified reports is designed to incentivise
seeking real-world confirmation rather than spamming submissions — but only
weakly. A production version would add cooldown windows + duplicate detection
via embedding similarity on summary text.
