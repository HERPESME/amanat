"""The seam where a ceiling comes from.

Deliberately an interface rather than a hard dependency on our own model.

The reasoning is competitive, not aesthetic. Razorpay already ships RTO Shield,
RTO Insights, and risk-tiered COD pricing built on their own transaction data. A
two-week project cannot beat that and should not pretend to — so risk enters
through a seam that a production deployment fills with the incumbent's engine,
and the model in `model.py` is the reference implementation that proves the seam
is real.

What the project actually contributes is the layer *around* the number: what the
system does with a ceiling under uncertainty, whether it can justify the choice,
and whether it refuses to exceed it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class CeilingAdvice:
    """A proposed ceiling, with enough context for the agent to justify it."""

    amount: int                  # paise
    basis: str                   # human-readable justification, goes in the chain
    nominal_coverage: float      # the claimed P(actual <= amount)
    source: str                  # which implementation produced this

    def as_reason(self) -> str:
        return (f"{self.basis} (source: {self.source}, "
                f"nominal coverage {self.nominal_coverage:.0%})")


@runtime_checkable
class CeilingSource(Protocol):
    """Anything that can propose a ceiling for a purchase."""

    name: str

    def advise(self, features: dict) -> CeilingAdvice: ...


class FixedMarginCeiling:
    """Ceiling = estimate x (1 + margin). The naive policy, and a real baseline.

    Included because it is what most systems would actually do, and because the
    frontier needs something to beat. It makes no coverage claim it can support —
    `nominal_coverage` is reported as 0.0 rather than invented.
    """

    name = "fixed-margin"

    def __init__(self, margin: float = 0.30) -> None:
        self.margin = margin

    def advise(self, features: dict) -> CeilingAdvice:
        estimate = int(features["estimate_paise"])
        amount = int(estimate * (1.0 + self.margin))
        return CeilingAdvice(
            amount=amount,
            basis=f"estimate plus a flat {self.margin:.0%} margin",
            nominal_coverage=0.0,   # no calibration, so no honest claim available
            source=self.name,
        )


class ConformalCeiling:
    """Ceiling from a conformalized quantile model. The reference implementation.

    Reports the *nominal* coverage. Whether the empirical coverage matches is an
    open question under temporal drift — see `frontier.py`, which measures the
    gap rather than assuming it away.
    """

    name = "conformal-quantile"

    def __init__(self, model, feature_order: list[str]) -> None:
        self.model = model
        self.feature_order = feature_order

    def advise(self, features: dict) -> CeilingAdvice:
        import numpy as np

        row = np.array([[float(features[k]) for k in self.feature_order]])
        amount = int(round(float(self.model.ceiling(row)[0]) * 100))
        return CeilingAdvice(
            amount=amount,
            basis=(f"conformalized quantile at alpha={self.model.alpha:g}, "
                   f"calibrated on held-out data"),
            nominal_coverage=1.0 - self.model.alpha,
            source=self.name,
        )


class ExternalCeiling:
    """Seam for a production risk engine — e.g. Razorpay RTO Shield.

    Not implemented, and deliberately so: it needs merchant credentials this
    project does not have, and re-implementing a system trained on 100+ billion
    data points would be worse than consuming it. Present so the integration
    point is explicit rather than hand-waved in a slide.
    """

    name = "external-risk-engine"

    def __init__(self, client=None) -> None:
        self.client = client

    def advise(self, features: dict) -> CeilingAdvice:
        raise NotImplementedError(
            "ExternalCeiling is an integration seam, not an implementation. "
            "A production deployment injects a merchant's existing risk engine "
            "here (e.g. Razorpay RTO Shield). Use ConformalCeiling for the "
            "reference behaviour."
        )
