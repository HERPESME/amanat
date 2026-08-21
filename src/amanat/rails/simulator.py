"""A rail simulator that enforces the capability table it is documented against.

Every behaviour here traces to an entry in `semantics.RAILS`. Where the table
says UNVERIFIED, the simulator refuses — the same answer the policy engine
gives — so the two layers cannot quietly disagree about what a rail permits.

The customer-side balance is modelled because the interesting failure is a
*ceiling set too low*, which is not a policy failure at all: policy allows it,
and the rail rejects it. That distinction is the point of having both layers.
"""
from __future__ import annotations

import itertools

from amanat.rails.base import BlockRef, BlockState, RailError
from amanat.rails.semantics import RAILS


class SimulatedRail:
    """In-memory rail. Honest about which semantics it is allowed to offer."""

    def __init__(self, rail_id: str, customer_balance: int = 10_000_00) -> None:
        if rail_id not in RAILS:
            raise RailError(f"unknown rail {rail_id!r}")
        self.rail_id = rail_id
        self.profile = RAILS[rail_id]
        self.customer_balance = customer_balance
        self._ids = itertools.count(1)
        self._blocks: dict[str, BlockRef] = {}

    # -- lifecycle ---------------------------------------------------------

    def reserve(self, ceiling: int, payee: str) -> BlockRef:
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

    def debit(self, ref: BlockRef, amount: int) -> BlockRef:
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

    def release(self, ref: BlockRef, amount: int | None = None) -> BlockRef:
        self._require_live(ref)
        amount = ref.available if amount is None else amount
        if amount > ref.available:
            raise RailError(f"cannot release {amount}; only {ref.available} unspent")
        ref.released += amount
        self.customer_balance += amount
        ref.events.append(f"RELEASE {amount}")
        if ref.available == 0:
            ref.state = BlockState.SETTLED
        return ref

    def revoke(self, ref: BlockRef) -> BlockRef:
        """The customer tearing the block down — always available to them."""
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
