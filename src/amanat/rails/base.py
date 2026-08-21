"""The rail interface every adapter implements.

Deliberately small. A rail can place a ceiling, move money against it, hand the
difference back, and tear the whole thing down. Everything else is the policy
engine's business, not the rail's.

The simulator is a **first-class implementation**, not a stand-in. Real-rail
access (SBMD, Cashfree pre-auth) requires merchant activation that a two-week
build cannot assume. What the simulator must never do is model semantics the
capability table has not evidenced — it enforces the same table the policy
engine reads, so the two cannot drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class RailError(Exception):
    """The rail refused. Distinct from a policy refusal, which happens earlier."""


class BlockState(Enum):
    IDLE = "idle"
    BLOCKED = "blocked"      # ceiling standing, nothing moved
    SETTLED = "settled"      # debited and the remainder released
    REVOKED = "revoked"      # torn down by the customer or by expiry


@dataclass
class BlockRef:
    """A handle on funds standing against a rail."""

    block_id: str
    rail_id: str
    ceiling: int                       # paise
    state: BlockState = BlockState.BLOCKED
    debited: int = 0
    released: int = 0
    events: list[str] = field(default_factory=list)

    @property
    def available(self) -> int:
        return self.ceiling - self.debited - self.released


@runtime_checkable
class RailAdapter(Protocol):
    """What every rail must be able to do."""

    rail_id: str

    def reserve(self, ceiling: int, payee: str) -> BlockRef: ...
    def debit(self, ref: BlockRef, amount: int) -> BlockRef: ...
    def release(self, ref: BlockRef, amount: int | None = None) -> BlockRef: ...
    def revoke(self, ref: BlockRef) -> BlockRef: ...
    def status(self, ref: BlockRef) -> BlockState: ...
