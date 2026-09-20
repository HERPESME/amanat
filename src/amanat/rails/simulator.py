"""A rail simulator that enforces the capability table it is documented against.

Every behaviour here traces to an entry in `semantics.RAILS`. Where the table
says UNVERIFIED, the simulator refuses — the same answer the policy engine
gives — so the two layers cannot quietly disagree about what a rail permits.

The customer-side balance is modelled because the interesting failure is a
*ceiling set too low*, which is not a policy failure at all: policy allows it,
and the rail rejects it. That distinction is the point of having both layers.

It also models the one property recovery depends on: an idempotency key. A call
repeated with the same key returns the first result without acting again, so a
caller that does not know whether its request landed can safely send it again.
Whether a *real* rail honours such a key is a per-rail fact to be measured, not
assumed; this simulator offers it so the session's recovery path can be proved.
"""
from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from amanat.rails.base import BlockRef, BlockState, RailError
from amanat.rails.semantics import RAILS


class SimulatedRail:
    """In-memory rail. Honest about which semantics it is allowed to offer."""

    supports_idempotency = True

    def __init__(self, rail_id: str, customer_balance: int = 10_000_00) -> None:
        if rail_id not in RAILS:
            raise RailError(f"unknown rail {rail_id!r}")
        self.rail_id = rail_id
        self.profile = RAILS[rail_id]
        self.customer_balance = customer_balance
        self._ids = itertools.count(1)
        self._blocks: dict[str, BlockRef] = {}
        self._seen: dict[str, tuple[tuple, Any]] = {}

    @property
    def blocks(self) -> tuple[BlockRef, ...]:
        return tuple(self._blocks.values())

    def get_block(self, block_id: str) -> BlockRef:
        try:
            return self._blocks[block_id]
        except KeyError:
            raise RailError(f"unknown block {block_id!r}") from None

    def _once(self, key: str | None, request: tuple, apply: Callable[[], Any]) -> Any:
        """Apply `apply` at most once per key; a rail refusal does not burn the key."""
        if key is None:
            return apply()
        seen = self._seen.get(key)
        if seen is not None:
            if seen[0] != request:
                raise RailError(
                    f"idempotency key {key!r} was already used for a different request")
            return seen[1]
        result = apply()
        self._seen[key] = (request, result)
        return result

    # -- lifecycle ---------------------------------------------------------

    def reserve(self, ceiling: int, payee: str, *,
                idempotency_key: str | None = None) -> BlockRef:
        def apply() -> BlockRef:
            if ceiling > self.customer_balance:
                raise RailError(
                    f"insufficient funds: ceiling {ceiling} exceeds customer balance "
                    f"{self.customer_balance}")
            ref = BlockRef(block_id=f"blk_{next(self._ids)}", rail_id=self.rail_id,
                           ceiling=ceiling)
            ref.events.append(f"BLOCK {ceiling} for {payee}")
            self.customer_balance -= ceiling
            self._blocks[ref.block_id] = ref
            return ref
        return self._once(idempotency_key, ("reserve", ceiling, payee), apply)

    def debit(self, ref: BlockRef, amount: int, *,
              idempotency_key: str | None = None) -> BlockRef:
        def apply() -> BlockRef:
            self._require_live(ref)
            if amount > ref.available:
                # The ceiling was set too low. Policy permitted this; the rail does
                # not. This is the graceful-failure path the demo is built around.
                raise RailError(
                    f"debit {amount} exceeds available block {ref.available}")
            is_partial = amount < ref.available
            if is_partial and not self.profile.permits("partial_debit"):
                decision = self.profile.explain("partial_debit")
                raise RailError(
                    f"{self.profile.display_name} refuses partial debit: "
                    f"{decision.reason}")
            ref.debited += amount
            ref.events.append(f"DEBIT {amount}")
            return ref
        return self._once(idempotency_key, ("debit", ref.block_id, amount), apply)

    def release(self, ref: BlockRef, amount: int | None = None, *,
                idempotency_key: str | None = None) -> BlockRef:
        """Return unspent funds — by teardown unless the rail offers better.

        Most rails do not offer better. A survey of six merchant-side PSPs found
        exactly one (Setu) exposing a modify that preserves the mandate;
        Razorpay, Cashfree, PayU, Juspay and BoxPay expose teardown only —
        Cashfree: "Only the CANCEL action is supported for SBMD subscriptions."

        So on a typical rail, handing back the change means revoking, which
        returns *all* of it and kills the mandate. Since OC-228 permits only one
        block at a time per merchant, the next purchase then needs fresh
        authentication. That cost is real and belongs in the model, so the
        simulator refuses to pretend otherwise.
        """
        def apply() -> BlockRef:
            self._require_live(ref)

            if not self.profile.permits("remainder_release_without_teardown"):
                if amount is not None and amount < ref.available:
                    raise RailError(
                        f"{self.profile.display_name} exposes no partial release: "
                        f"returning any part of the block tears down all of it. "
                        f"Release {ref.available} or nothing.")
                ref.events.append("RELEASE via teardown (rail exposes no partial release)")
                return self._revoke(ref)

            released = ref.available if amount is None else amount
            if released > ref.available:
                raise RailError(f"cannot release {released}; only {ref.available} unspent")
            ref.released += released
            self.customer_balance += released
            ref.events.append(f"RELEASE {released} (mandate preserved)")
            if ref.available == 0:
                ref.state = BlockState.SETTLED
            return ref
        return self._once(idempotency_key, ("release", ref.block_id, amount), apply)

    def revoke(self, ref: BlockRef, *, idempotency_key: str | None = None) -> BlockRef:
        """The customer tearing the block down — always available to them."""
        return self._once(idempotency_key, ("revoke", ref.block_id),
                          lambda: self._revoke(ref))

    def _revoke(self, ref: BlockRef) -> BlockRef:
        self._require_live(ref)
        refunded = ref.available
        self.customer_balance += refunded
        ref.released += refunded
        ref.state = BlockState.REVOKED
        ref.events.append(f"REVOKE (returned {refunded})")
        return ref

    def status(self, ref: BlockRef) -> BlockState:
        return ref.state

    def _require_live(self, ref: BlockRef) -> None:
        if ref.state is not BlockState.BLOCKED:
            raise RailError(f"block {ref.block_id} is {ref.state.value}, not live")
