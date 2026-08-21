"""Shared fixtures.

`verified_rail` registers a rail whose partial-debit support carries a primary
citation. It used to stand in for SBMD, which refused partial debit while the
capability rested on vendor docs. NPCI/UPI/OC-228/2025-26 was read on
21 Aug 2026 and SBMD now permits it on its own citation — see
`test_partial_debit_on_sbmd_is_permitted_and_rests_on_oc228`. The fixture stays
because the property under test is that the gate keys off the *evidence tier*,
which needs a rail that is not SBMD for the assertion to mean anything.
"""
from datetime import datetime, timedelta, timezone

import pytest

from amanat.policy.envelope import Envelope
from amanat.rails.semantics import (
    RAILS, Capability, RailProfile, SourceTier,
)

VERIFIED_RAIL_ID = "_verified_fixture"


@pytest.fixture
def verified_rail():
    rail = RailProfile(
        rail_id=VERIFIED_RAIL_ID, display_name="Verified Fixture Rail",
        capabilities=[
            Capability(
                name="partial_debit", supported=True,
                source_tier=SourceTier.PRIMARY,
                citation="fixture", url="https://example.test",
                quote="partial debit against a standing block is permitted",
            ),
        ],
    )
    RAILS[rail.rail_id] = rail
    yield rail.rail_id
    RAILS.pop(rail.rail_id, None)


@pytest.fixture
def envelope():
    return Envelope(
        subject="order-test",
        max_total=1_000_00,
        max_per_txn=800_00,
        allowed_payees=["citycabs"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        intent_text="Book a cab, cap it at 1000 rupees.",
    )


MODIFIABLE_RAIL_ID = "_setu_like_fixture"


@pytest.fixture
def modifiable_rail():
    """A rail that can return the remainder WITHOUT tearing the block down.

    Stands in for Setu, the only one of six surveyed merchant-side PSPs that
    exposes a mandate modify preserving the block. Razorpay, Cashfree, PayU,
    Juspay and BoxPay expose teardown only, so this is the exception rather
    than the shape to assume.
    """
    rail = RailProfile(
        rail_id=MODIFIABLE_RAIL_ID, display_name="Setu-like Fixture Rail",
        capabilities=[
            Capability(
                name="partial_debit", supported=True,
                source_tier=SourceTier.PRIMARY,
                citation="fixture", url="https://example.test",
                quote="partial debit against a standing block is permitted",
            ),
            Capability(
                name="remainder_release_without_teardown", supported=True,
                source_tier=SourceTier.PRIMARY,
                citation="fixture", url="https://example.test",
                quote="the mandate amount may be changed without revoking the mandate",
            ),
        ],
    )
    RAILS[rail.rail_id] = rail
    yield rail.rail_id
    RAILS.pop(rail.rail_id, None)
