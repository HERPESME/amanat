"""NYC TLC trip records — real fare labels for the ceiling problem.

Why this dataset. The ceiling problem needs (a) a purchase whose final amount is
genuinely unknown at commit time, and (b) real labels for what that amount turned
out to be. Metered taxi fares are exactly that, and the TLC publishes them.

The alternative — generating synthetic fares — would make every downstream number
circular: a model trained on data you invented, evaluated against the same
generator, proves only that you can sample from your own distribution. There is
no public Indian COD-RTO dataset; say so rather than manufacturing one.

Split is **temporal**, never random. Training on January and testing on February
is the honest analogue of deployment. A random split over a time series leaks
future information through shared demand conditions and inflates coverage.

Source: https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE = "https://d37ci6vzurychx.cloudfront.net/trip-data"
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Features knowable when the agent must commit to a ceiling — i.e. at booking,
# before the meter has run. Anything realised during the trip is leakage.
BOOKING_FEATURES = ["PULocationID", "DOLocationID", "hour", "dow", "passenger_count"]

# A real dispatch system also knows the ROUTE distance at booking, from its
# routing engine. We proxy that with realised trip_distance, which is optimistic:
# actual routes deviate from planned ones. Both variants are reported so the
# optimism is visible rather than buried.
DISPATCH_FEATURES = BOOKING_FEATURES + ["trip_distance"]


def download(month: str) -> Path:
    """Fetch one month of yellow-taxi records. Cached on disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"yellow_tripdata_{month}.parquet"
    if not path.exists():
        url = f"{BASE}/yellow_tripdata_{month}.parquet"
        print(f"  downloading {url} ...")
        urllib.request.urlretrieve(url, path)
    return path


def load(month: str, sample: int | None = None,
         seed: int = 0) -> pd.DataFrame:
    """Load and clean one month into (features, target).

    Target is the **metered amount**: what the rail would have to debit. Tip is
    excluded — it is authorized separately and after the fact, so including it
    would be predicting something the ceiling never has to cover.
    """
    df = pd.read_parquet(download(month), columns=[
        "tpep_pickup_datetime", "PULocationID", "DOLocationID",
        "passenger_count", "trip_distance", "total_amount", "tip_amount",
    ])

    df["metered_amount"] = df["total_amount"] - df["tip_amount"]

    # Cleaning, each rule for a stated reason.
    df = df[df["metered_amount"].between(3.0, 250.0)]      # drop refunds and outliers
    df = df[df["trip_distance"].between(0.1, 100.0)]       # drop null/garbage trips
    df = df[df["passenger_count"].between(1, 6)]           # drop unset passenger counts

    ts = pd.to_datetime(df["tpep_pickup_datetime"])
    df["hour"] = ts.dt.hour
    df["dow"] = ts.dt.dayofweek

    # Guard against the parquet containing stray months (TLC files do bleed).
    df = df[ts.dt.strftime("%Y-%m") == month]

    df = df.dropna(subset=BOOKING_FEATURES + ["metered_amount"])

    if sample is not None and len(df) > sample:
        df = df.sample(sample, random_state=seed)

    return df.reset_index(drop=True)


def train_calib_test(train_month: str = "2024-01", test_month: str = "2024-02",
                     sample: int = 200_000, calib_frac: float = 0.3,
                     features: list[str] | None = None, seed: int = 0,
                     calib_mode: str = "recent"):
    """Temporal split, with a calibration slice carved out of training.

    Conformal prediction needs a calibration set the quantile models never saw.
    Taking it from the training month keeps the test month a genuine hold-out.

    `calib_mode` decides *which* training rows calibrate, and it matters more
    than it looks:

      "random" — a uniform slice of the training month. This is the textbook
                 split, and it satisfies exchangeability *within* January. But
                 deployment is February, so the guarantee it produces is a
                 guarantee about the wrong month.
      "recent" — the chronologically last rows of the training month. Breaks
                 exchangeability with the training set on purpose, in exchange
                 for calibrating on the conditions closest to deployment.

    Conformal's coverage guarantee is distribution-free but NOT shift-free. Under
    temporal drift neither mode restores it exactly; "recent" narrows the gap.
    Both are reported so the size of that gap is visible.
    """
    features = features or DISPATCH_FEATURES
    tr = load(train_month, sample=sample, seed=seed)
    te = load(test_month, sample=sample // 2, seed=seed)

    if calib_mode == "recent":
        tr = tr.sort_values("tpep_pickup_datetime").reset_index(drop=True)
        cut = int(len(tr) * (1.0 - calib_frac))
        mask = np.zeros(len(tr), dtype=bool)
        mask[cut:] = True
    elif calib_mode == "random":
        mask = np.random.default_rng(seed).random(len(tr)) < calib_frac
    else:
        raise ValueError(f"unknown calib_mode {calib_mode!r}")

    return (
        tr.loc[~mask, features].to_numpy(float), tr.loc[~mask, "metered_amount"].to_numpy(),
        tr.loc[mask, features].to_numpy(float),  tr.loc[mask, "metered_amount"].to_numpy(),
        te[features].to_numpy(float),            te["metered_amount"].to_numpy(),
    )
