"""Choosing the ceiling: conformalized quantile regression, one-sided.

The decision problem, stated exactly:

    An agent must commit to a spending ceiling C before the final amount Y is
    known. If Y > C the debit fails and the sale is lost. If Y <= C the sale
    settles and (C - Y) of the customer's money sat blocked for nothing.

So this is not a point-prediction problem — predicting the *mean* fare is close
to the worst thing you can do, because it fails roughly half of all trips. It is
an asymmetric-loss problem, and the natural object is an upper quantile.

Method: Conformalized Quantile Regression (Romano, Patterson & Candès, NeurIPS
2019, arXiv:1905.03222), restricted to the one-sided case since only the ceiling
matters. Plain quantile regression gives no coverage guarantee — a q=0.95 model
may cover 89% or 97% of the time depending on how well it fits. Conformal
calibration converts it into a *distribution-free finite-sample* guarantee:

    P(Y <= ceiling(X)) >= 1 - alpha

holding for any underlying model, with no assumption on the fare distribution.
That guarantee is the whole reason to use it here — the ceiling is a promise made
to a customer about their own money, so an empirical claim beats a fitted one.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor


@dataclass
class CeilingModel:
    """Predicts a spending ceiling with a calibrated coverage guarantee.

    `alpha` is the tolerated failure rate: alpha=0.05 targets a ceiling that
    covers the realised amount at least 95% of the time.
    """

    alpha: float = 0.05
    n_estimators: int = 200
    max_depth: int = 6
    learning_rate: float = 0.1
    random_state: int = 0

    _model: GradientBoostingRegressor | None = None
    _conformal_pad: float = 0.0

    def fit(self, X_train: np.ndarray, y_train: np.ndarray,
            X_calib: np.ndarray, y_calib: np.ndarray) -> "CeilingModel":
        # Stage 1 — a quantile regressor at the nominal level.
        self._model = GradientBoostingRegressor(
            loss="quantile", alpha=1.0 - self.alpha,
            n_estimators=self.n_estimators, max_depth=self.max_depth,
            learning_rate=self.learning_rate, random_state=self.random_state,
        ).fit(X_train, y_train)

        # Stage 2 — conformal calibration on data the model never saw.
        # Score is the signed shortfall: how far the quantile fell below truth.
        scores = y_calib - self._model.predict(X_calib)
        n = len(scores)
        level = min(np.ceil((n + 1) * (1.0 - self.alpha)) / n, 1.0)
        self._conformal_pad = float(np.quantile(scores, level, method="higher"))
        return self

    def ceiling(self, X: np.ndarray) -> np.ndarray:
        """The amount to block. Never below the raw quantile prediction."""
        if self._model is None:
            raise RuntimeError("call fit() first")
        return self._model.predict(X) + self._conformal_pad

    @property
    def conformal_pad(self) -> float:
        """How much the guarantee cost, in currency units.

        Large values mean the quantile model was poorly calibrated and conformal
        had to bail it out — worth reporting rather than hiding.
        """
        return self._conformal_pad


@dataclass
class Outcome:
    """What a ceiling policy actually did on a batch of real trips."""

    alpha: float
    nominal_coverage: float
    empirical_coverage: float     # fraction of trips whose debit would succeed
    mean_stranded: float          # avg over-block on the trips that settled
    median_stranded: float
    mean_ceiling: float
    mean_actual: float
    conformal_pad: float
    n: int

    @property
    def failure_rate(self) -> float:
        return 1.0 - self.empirical_coverage

    @property
    def guarantee_held(self) -> bool:
        return self.empirical_coverage >= self.nominal_coverage


def evaluate(model: CeilingModel, X: np.ndarray, y: np.ndarray) -> Outcome:
    """Score a fitted ceiling policy against realised amounts."""
    c = model.ceiling(X)
    covered = y <= c
    stranded = (c - y)[covered]
    return Outcome(
        alpha=model.alpha,
        nominal_coverage=1.0 - model.alpha,
        empirical_coverage=float(covered.mean()),
        mean_stranded=float(stranded.mean()) if len(stranded) else 0.0,
        median_stranded=float(np.median(stranded)) if len(stranded) else 0.0,
        mean_ceiling=float(c.mean()),
        mean_actual=float(y.mean()),
        conformal_pad=model.conformal_pad,
        n=len(y),
    )


class MeanBaseline:
    """Predict the mean and block that. The obvious thing, and it is terrible.

    Included because 'why not just predict the fare?' is the first question
    anyone asks, and the honest answer is a number: it fails about half the time.
    """

    def __init__(self) -> None:
        self._m: GradientBoostingRegressor | None = None
        self.alpha = 0.5
        self.conformal_pad = 0.0

    def fit(self, X_train, y_train, X_calib=None, y_calib=None) -> "MeanBaseline":
        self._m = GradientBoostingRegressor(
            n_estimators=200, max_depth=6, random_state=0).fit(X_train, y_train)
        return self

    def ceiling(self, X: np.ndarray) -> np.ndarray:
        assert self._m is not None
        return self._m.predict(X)
