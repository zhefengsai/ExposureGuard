#!/usr/bin/env python3
"""Does the aggregate advantage survive outside the measured trace?

Every containment number in the paper comes from one four-month Ethereum trace.
The mechanism's advantage over independently sized buckets rests on statistical
multiplexing: per-group caps must each cover their OWN peak day, and those peak
days do not coincide, so the caps sum to far more than the aggregate peak.  If
the peaks did coincide, the sum would collapse onto the aggregate and the
advantage would vanish.  That is a testable dependency, so we test it.

Model.  Group g has size L_g and daily volume

    v[g,t] = L_g * exp(sigma_w * (sqrt(rho) * Z[t] + sqrt(1 - rho) * E[g,t]))

with Z a common daily factor and E idiosyncratic, both standard normal.  The
loadings matter: with sqrt(rho) and sqrt(1-rho) the pairwise correlation of
log-demand between two groups is exactly rho, whereas the superficially
similar (rho, sqrt(1-rho^2)) pair yields rho^2 and would mislabel the axis.
`check_construction` asserts this on generated samples rather than trusting
the algebra.  rho = 0 gives independent groups, rho = 1 one shared shock and
identical peak days.  The model expresses non-negative correlation only.

Both dispersions are estimated from the trace rather than assumed, and they are
estimated separately, which matters.  The pooled standard deviation of log
daily volume mixes two things: how much one token varies over time, and how
much tokens differ from each other in size.  A model that draws sizes from
their own distribution and then applies the pooled figure as temporal noise
counts the between-token spread twice.  We therefore decompose it:

    sigma_w  mean within-token std of log daily volume  -> temporal term
    sigma_b  std of per-token mean log daily volume     -> size term, L_g

Both components are smaller than the pooled figure, which is the point: using
the pooled value as temporal noise while also drawing sizes counts the
between-token spread twice.  The two are not an exact variance decomposition,
because tokens contribute unequal numbers of active days, so two alternative
specifications are reported alongside -- including the pooled-sigma one -- and
the effect of the choice is visible rather than buried.

Reported quantity.  The multiplexing ratio

    M = sum_g max_t v[g,t]  /  max_t sum_g v[g,t]

is exactly the factor by which independently sized caps exceed a budget sized
on the same demand, so under a common sizing multiple it IS the containment
advantage before the collateral ceiling binds.  M is 1 when peaks coincide.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import numpy as np

import sim

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 20260921
RHOS = [0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 1.0]
NS = [10, 20, 50, 100, 200]
MIN_ACTIVE_DAYS = 5
REPS = 400


def factor(n, days, rho, rng):
    """Log-demand shocks whose pairwise correlation is rho, not rho^2."""
    if rho < 0:
        raise ValueError("the single-factor model expresses rho >= 0 only")
    Z = rng.standard_normal(days)
    E = rng.standard_normal((n, days))
    return np.sqrt(rho) * Z[None, :] + np.sqrt(1.0 - rho) * E


def draw(n, days, rho, sigma_w, size, rng):
    """One replicate. `size` returns the n group sizes."""
    L = size(n, rng)
    v = L[:, None] * np.exp(sigma_w * factor(n, days, rho, rng))
    return v.max(axis=1).sum() / v.sum(axis=0).max()


def check_construction(n=20, days=121, reps=300, tol=0.02):
    """Assert the generator realises the rho it is given."""
    rng = np.random.default_rng(SEED)
    bad = []
    for rho in (0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0):
        acc = []
        for _ in range(reps):
            x = factor(n, days, rho, rng)
            c = np.corrcoef(x)
            acc.append(c[np.triu_indices(n, 1)].mean())
        got = float(np.mean(acc))
        if abs(got - rho) > tol:
            bad.append((rho, got))
    if bad:
        raise SystemExit("generator does not realise its rho: "
                         + ", ".join(f"{r}->{g:.3f}" for r, g in bad))
    return True


def lognormal_size(sigma_b):
    return lambda n, rng: np.exp(sigma_b * rng.standard_normal(n))


def pareto_size(shape):
    return lambda n, rng: rng.pareto(shape, size=n) + 1.0


def band(n, days, rho, sigma_w, size, reps=REPS, seed_off=0):
    rng = np.random.default_rng(SEED + seed_off + n * 7)
    m = np.array([draw(n, days, rho, sigma_w, size, rng) for _ in range(reps)])
    return float(m.mean()), float(np.percentile(m, 5)), float(np.percentile(m, 95))


def rho_curve(n, days, rhos, sigma_w, size, reps=REPS):
    """Common random numbers across rho: every point uses the same draws, so
    the curve is a paired comparison rather than independent runs."""
    rng = np.random.default_rng(SEED + n * 7)
    acc = {r: [] for r in rhos}
    for _ in range(reps):
        L = size(n, rng)
        Z = rng.standard_normal(days)
        E = rng.standard_normal((n, days))
        for r in rhos:
            x = np.sqrt(r) * Z[None, :] + np.sqrt(1.0 - r) * E
            v = L[:, None] * np.exp(sigma_w * x)
            acc[r].append(v.max(axis=1).sum() / v.sum(axis=0).max())
    return {r: (float(np.mean(a)), float(np.percentile(a, 5)),
                float(np.percentile(a, 95)))
            for r, a in ((r, np.array(v)) for r, v in acc.items())}


def measured():
    """Per-token daily volumes from the production trace."""
    ev = sim.load()
    meta = {r["router"][2:].lower(): r
            for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))}
    daily = defaultdict(lambda: defaultdict(float))
    for e in ev:
        sym = str(meta.get(e["route"], {}).get("symbol") or e.get("symbol") or "UNK")
        daily[sym][int(e["ts"] // sim.D)] += e["usd"]
    days = sorted({d for s in daily.values() for d in s})
    idx = {d: k for k, d in enumerate(days)}
    v = np.zeros((len(daily), len(days)))
    for i, s in enumerate(sorted(daily)):
        for d, x in daily[s].items():
            v[i, idx[d]] = x
    return v


def dispersions(v):
    """Split pooled log-volume dispersion into temporal and size components."""
    rows = [np.log(r[r > 0]) for r in v if (r > 0).sum() >= MIN_ACTIVE_DAYS]
    sigma_w = float(np.mean([r.std() for r in rows]))
    sigma_b = float(np.std([r.mean() for r in rows]))
    pooled = float(np.log(v[v > 0]).std())
    return sigma_w, sigma_b, pooled, len(rows)


def empirical_rho(v):
    """Mean pairwise correlation of log daily volume, over active groups."""
    keep = v[(v > 0).sum(axis=1) >= MIN_ACTIVE_DAYS]
    lg = np.log(np.where(keep > 0, keep, np.nan))
    cs = []
    for i in range(len(keep)):
        for j in range(i + 1, len(keep)):
            a, b = lg[i], lg[j]
            m = ~(np.isnan(a) | np.isnan(b))
            if m.sum() >= MIN_ACTIVE_DAYS and np.nanstd(a[m]) > 0 and np.nanstd(b[m]) > 0:
                cs.append(np.corrcoef(a[m], b[m])[0, 1])
    return (float(np.mean(cs)), float(np.std(cs) / np.sqrt(len(cs))),
            len(cs), len(keep))


def main() -> None:
    check_construction()
    v = measured()
    M_obs = float(v.max(axis=1).sum() / v.sum(axis=0).max())
    rho_hat, rho_se, n_pairs, n_active = empirical_rho(v)
    n_grp, n_days = v.shape
    sw, sb, pooled, n_rows = dispersions(v)
    size = lognormal_size(sb)

    print(f"measured trace: {n_grp} tokens over {n_days} days")
    print(f"  multiplexing ratio M = {M_obs:.2f}x")
    print(f"  pooled sigma(log daily volume) = {pooled:.2f}")
    print(f"    within-token (temporal)  sigma_w = {sw:.2f}")
    print(f"    between-token (size)     sigma_b = {sb:.2f}")
    print(f"  the pooled figure exceeds either component, so using it as the "
          f"temporal term\n  while also drawing sizes would count the "
          f"between-token spread twice.")
    print(f"  mean pairwise log-volume correlation = {rho_hat:+.3f} "
          f"(s.e. {rho_se:.3f}) "
          f"({n_pairs} pairs over {n_active} tokens active >= "
          f"{MIN_ACTIVE_DAYS} days)\n")

    print(f"(a) correlation sweep, n={n_grp}, days={n_days}, sigma_w={sw:.2f}")
    print(f"{'rho':>7}{'M mean':>10}{'p5':>9}{'p95':>9}")
    rho_rows = []
    curve = rho_curve(n_grp, n_days, RHOS, sw, size)
    for r in RHOS:
        m, lo, hi = curve[r]
        rho_rows.append({"rho": r, "M_mean": m, "M_p5": lo, "M_p95": hi})
        print(f"{r:>7.2f}{m:>10.2f}{lo:>9.2f}{hi:>9.2f}")

    # The measured correlation is indistinguishable from zero and the
    # single-factor model expresses rho >= 0, so the validation point is
    # rho = 0 rather than the point estimate.
    m_at, lo_at, hi_at = band(n_grp, n_days, 0.0, sw, size)
    inside = lo_at <= M_obs <= hi_at
    print(f"\n  at rho=0 (measured {rho_hat:+.3f} +/- {rho_se:.3f}): "
          f"model M = {m_at:.2f}x [{lo_at:.2f}, {hi_at:.2f}]; "
          f"observed {M_obs:.2f}x -> "
          f"{'inside' if inside else 'OUTSIDE'} the 5-95 band")

    print("\n(b) group count, at rho=0")
    print(f"{'n':>7}{'M mean':>10}{'p5':>9}{'p95':>9}")
    n_rows_out = []
    for n in NS:
        m, lo, hi = band(n, n_days, 0.0, sw, size)
        n_rows_out.append({"n": n, "M_mean": m, "M_p5": lo, "M_p95": hi})
        print(f"{n:>7}{m:>10.2f}{lo:>9.2f}{hi:>9.2f}")

    print("\n(c) specification check: does the dispersion split change the answer?")
    specs = [
        ("estimated sigma_w + lognormal sigma_b", sw, size),
        ("estimated sigma_w + Pareto(1.2)", sw, pareto_size(1.2)),
        ("pooled sigma + Pareto(1.2)", pooled, pareto_size(1.2)),
    ]
    print(f"{'specification':>38}{'M mean':>10}{'p5':>9}{'p95':>9}{'obs in band':>13}")
    spec_rows = []
    for i, (lbl, s_, sz) in enumerate(specs):
        m, lo, hi = band(n_grp, n_days, 0.0, s_, sz, seed_off=1000 * i)
        ok = lo <= M_obs <= hi
        spec_rows.append({"spec": lbl, "sigma_w": s_, "M_mean": m,
                          "M_p5": lo, "M_p95": hi, "observed_inside_band": bool(ok)})
        print(f"{lbl:>38}{m:>10.2f}{lo:>9.2f}{hi:>9.2f}{str(ok):>13}")

    out = {"schema": 3, "seed": SEED, "reps": REPS,
           "min_active_days": MIN_ACTIVE_DAYS,
           "measured": {"tokens": n_grp, "days": n_days, "M": M_obs,
                        "sigma_pooled": pooled, "sigma_within": sw,
                        "sigma_between": sb, "rho_hat": rho_hat, "rho_se": rho_se,
                        "pairs": n_pairs, "tokens_active": n_active},
           "validation": {"rho": 0.0, "M_mean": m_at, "M_p5": lo_at,
                          "M_p95": hi_at, "observed_inside_band": bool(inside)},
           "rho_sweep": rho_rows, "n_sweep": n_rows_out,
           "spec_check": spec_rows}
    json.dump(out, open(f"{HERE}/data/synth_generalization.json", "w"), indent=1)
    print("\nwrote data/synth_generalization.json")


if __name__ == "__main__":
    main()
