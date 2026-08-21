"""The headline result: empirical coverage vs money stranded.

    uv run --extra ml python -m amanat.ceiling.frontier

There is no single right ceiling. Every choice trades a lost sale against blocked
customer money, and the exchange rate depends on margin and cost of capital,
which differ per merchant. So the deliverable is the **frontier**, not a number —
the merchant picks the point; the model's job is to make the curve as low as
possible and to make the coverage claim honest rather than hoped-for.

Two axes are varied deliberately, and both exist to expose an optimism rather
than to flatter the result:

  feature set   strict   — only what is knowable at booking. No leakage at all.
                dispatch — adds route distance, which a real dispatch system
                           does know at booking. Proxied by realised distance,
                           which is optimistic.

  calibration   random   — textbook conformal split, exchangeable within the
                           training month, guaranteeing coverage for the wrong month.
                recent   — the chronologically last training rows, closest to
                           deployment conditions.

The headline finding is in the `held?` column: under a temporal split, conformal
coverage is systematically **below nominal**. That is not a bug — see the note
printed at the end.
"""
from __future__ import annotations

import numpy as np

from amanat.ceiling import data as D
from amanat.ceiling.model import CeilingModel, MeanBaseline, evaluate

ALPHAS = [0.25, 0.10, 0.05, 0.02, 0.01]


def _row(label: str, o, extra: str = "") -> str:
    gap = (o.empirical_coverage - o.nominal_coverage) * 100
    flag = "\033[32m ok \033[0m" if o.guarantee_held else f"\033[31mMISS\033[0m"
    return (f"  {label:>10} │ {o.empirical_coverage * 100:6.2f}%  │ {gap:+5.2f}pp "
            f"│ {o.mean_stranded:7.2f}  │ {o.mean_ceiling:7.2f}  │ {flag} {extra}")


def run_for(features: list[str], name: str, calib_mode: str,
            sample: int = 200_000) -> list:
    Xtr, ytr, Xca, yca, Xte, yte = D.train_calib_test(
        sample=sample, features=features, calib_mode=calib_mode)

    print(f"\n\033[1m  {name}  ·  calibration: {calib_mode}\033[0m")
    print(f"  \033[2mtrain {len(ytr):,} · calibrate {len(yca):,} · test {len(yte):,} "
          f"│ test mean ${yte.mean():.2f}, median ${np.median(yte):.2f}\033[0m")
    print("     ceiling │ coverage │  gap vs │ mean     │ mean     │ guarantee")
    print("      policy │  (test)  │ nominal │ stranded │ ceiling  │  held?")
    print("  ───────────┼──────────┼─────────┼──────────┼──────────┼──────────")

    if calib_mode == "recent":
        bo = evaluate(MeanBaseline().fit(Xtr, ytr), Xte, yte)
        print(_row("mean pred", bo, "\033[2m← the obvious thing\033[0m"))

    out = []
    for a in ALPHAS:
        m = CeilingModel(alpha=a).fit(Xtr, ytr, Xca, yca)
        o = evaluate(m, Xte, yte)
        out.append((name, calib_mode, o))
        print(_row(f"CQR α={a:g}", o, f"\033[2mpad ${o.conformal_pad:+.2f}\033[0m"))
    return out


def main() -> None:
    print("\n\033[1mTHE CEILING FRONTIER\033[0m — how much money must be blocked to make")
    print("a debit succeed, measured on real NYC TLC metered fares.\n")
    print("  \033[2mFailure  = realised amount exceeded the ceiling → debit rejected → sale lost.")
    print("  Stranded = customer money blocked, then handed back unused.\033[0m")

    results = []
    for feats, label in ((D.BOOKING_FEATURES, "STRICT   (no leakage)"),
                         (D.DISPATCH_FEATURES, "DISPATCH (route distance at booking)")):
        print(f"\n\033[1m{'═' * 76}\033[0m")
        for mode in ("random", "recent"):
            results += run_for(feats, label, mode)

    misses = [o for _, _, o in results if not o.guarantee_held]
    print(f"\n\033[1m{'═' * 76}\n  THE FINDING\n{'═' * 76}\033[0m")
    print(f"  {len(misses)} of {len(results)} conformal guarantees came in BELOW nominal.\n")
    print("  Conformal prediction's finite-sample guarantee is distribution-free but")
    print("  \033[1mnot shift-free\033[0m: it assumes calibration and test data are exchangeable.")
    print("  A temporal split — train January, deploy February — breaks exactly that")
    print("  assumption, so the guarantee is systematically optimistic under the only")
    print("  conditions that matter.\n")
    print("  This is the honest version of the result. A random train/test split would")
    print("  have shown the guarantee holding cleanly, and would have been a lie about")
    print("  deployment. Calibrating on recent data narrows the gap without closing it;")
    print("  closing it needs either a slack factor or online recalibration.\n")
    print("  \033[2mRomano, Patterson & Candès, 'Conformalized Quantile Regression',")
    print("  NeurIPS 2019, arXiv:1905.03222 — see §2 for the exchangeability condition.\033[0m\n")


if __name__ == "__main__":
    main()
