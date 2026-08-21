"""Ceiling model mechanics, on synthetic *exchangeable* data.

These tests exist to answer one question precisely: when the frontier reports
that conformal coverage came in below nominal on real NYC data, is that a bug in
this implementation, or is it distribution shift?

Here the data is exchangeable by construction — one distribution, shuffled, split
at random. Conformal's guarantee applies. If coverage holds here and fails on the
temporal split, the implementation is correct and the shortfall is the shift.

Synthetic data is legitimate for this and only this: testing that an algorithm
implements its own guarantee. It would not be legitimate for the frontier itself,
which is why that runs on real fares.
"""
import numpy as np
import pytest

pytest.importorskip("sklearn")

from amanat.ceiling.model import CeilingModel, MeanBaseline, evaluate


def synthetic(n: int, seed: int = 0):
    """Heteroscedastic and right-skewed, like a fare — but exchangeable."""
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 10, size=(n, 3))
    base = 3.0 + 2.0 * X[:, 0] + 0.5 * X[:, 1]
    noise = rng.lognormal(mean=0.0, sigma=0.35 + 0.05 * X[:, 2], size=n)
    return X, base * noise


@pytest.fixture(scope="module")
def split():
    X, y = synthetic(9_000)
    return (X[:4000], y[:4000], X[4000:6500], y[4000:6500],
            X[6500:], y[6500:])


class TestConformalGuarantee:
    @pytest.mark.parametrize("alpha", [0.2, 0.1, 0.05])
    def test_coverage_holds_on_exchangeable_data(self, split, alpha):
        """The guarantee: P(Y <= ceiling) >= 1 - alpha.

        Allow a small sampling tolerance -- the guarantee is on the expectation,
        and the test set is finite.
        """
        Xtr, ytr, Xca, yca, Xte, yte = split
        m = CeilingModel(alpha=alpha).fit(Xtr, ytr, Xca, yca)
        o = evaluate(m, Xte, yte)
        assert o.empirical_coverage >= (1 - alpha) - 0.02, (
            f"nominal {1 - alpha:.2f}, empirical {o.empirical_coverage:.4f}"
        )

    def test_tighter_alpha_buys_coverage_with_stranded_money(self, split):
        """The core tradeoff must be monotone, or the frontier is meaningless."""
        Xtr, ytr, Xca, yca, Xte, yte = split
        loose = evaluate(CeilingModel(alpha=0.20).fit(Xtr, ytr, Xca, yca), Xte, yte)
        tight = evaluate(CeilingModel(alpha=0.02).fit(Xtr, ytr, Xca, yca), Xte, yte)
        assert tight.empirical_coverage > loose.empirical_coverage
        assert tight.mean_stranded > loose.mean_stranded

    def test_calibration_is_fit_on_held_out_data_only(self, split):
        """Calibrating on training data destroys the guarantee. Guard against it."""
        Xtr, ytr, _, _, Xte, yte = split
        leaky = CeilingModel(alpha=0.05).fit(Xtr, ytr, Xtr, ytr)
        honest = CeilingModel(alpha=0.05).fit(*split[:4])
        assert honest.conformal_pad >= leaky.conformal_pad - 1e-9


class TestMeanBaselineIsABadPolicy:
    def test_predicting_the_mean_fails_about_half_the_time(self, split):
        """'Why not just predict the fare?' — because this is what happens."""
        Xtr, ytr, _, _, Xte, yte = split
        o = evaluate(MeanBaseline().fit(Xtr, ytr), Xte, yte)
        assert o.empirical_coverage < 0.75


class TestOutcomeAccounting:
    def test_stranded_is_measured_only_on_settled_trips(self, split):
        """A failed debit strands nothing -- the block is released untouched."""
        Xtr, ytr, Xca, yca, Xte, yte = split
        m = CeilingModel(alpha=0.1).fit(Xtr, ytr, Xca, yca)
        c = m.ceiling(Xte)
        expected = float((c - yte)[yte <= c].mean())
        assert evaluate(m, Xte, yte).mean_stranded == pytest.approx(expected)

    def test_ceiling_never_below_the_raw_quantile(self, split):
        Xtr, ytr, Xca, yca, Xte, _ = split
        m = CeilingModel(alpha=0.05).fit(Xtr, ytr, Xca, yca)
        assert np.all(m.ceiling(Xte) >= m._model.predict(Xte) - 1e-9)

    def test_unfitted_model_refuses_to_predict(self):
        with pytest.raises(RuntimeError, match="fit"):
            CeilingModel().ceiling(np.zeros((2, 3)))


class TestCeilingSourceSeam:
    """Risk enters through a seam so a production engine can replace ours."""

    def test_fixed_margin_makes_no_coverage_claim_it_cannot_support(self):
        from amanat.ceiling.source import FixedMarginCeiling
        a = FixedMarginCeiling(margin=0.3).advise({"estimate_paise": 100_00})
        assert a.amount == 130_00
        assert a.nominal_coverage == 0.0

    def test_conformal_source_reports_its_nominal_coverage(self):
        from amanat.ceiling.model import CeilingModel
        from amanat.ceiling.source import ConformalCeiling
        X, y = synthetic(3_000)
        m = CeilingModel(alpha=0.05).fit(X[:1500], y[:1500], X[1500:], y[1500:])
        src = ConformalCeiling(m, ["a", "b", "c"])
        a = src.advise({"a": 5.0, "b": 5.0, "c": 5.0})
        assert a.nominal_coverage == 0.95
        assert a.amount > 0
        assert "conformal" in a.as_reason()

    def test_external_seam_refuses_to_pretend_it_is_implemented(self):
        from amanat.ceiling.source import ExternalCeiling
        with pytest.raises(NotImplementedError, match="integration seam"):
            ExternalCeiling().advise({})

    def test_all_sources_satisfy_the_protocol(self):
        from amanat.ceiling.source import (
            CeilingSource, ExternalCeiling, FixedMarginCeiling,
        )
        assert isinstance(FixedMarginCeiling(), CeilingSource)
        assert isinstance(ExternalCeiling(), CeilingSource)
