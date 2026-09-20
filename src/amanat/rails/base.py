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


class RailOutcomeUnknown(Exception):
    """The call may or may not have acted: a timeout status, a server error.

    Not a `RailError` on purpose. A refusal means nothing happened; silence means the rail may have
    acted, and the session must stop moving money until the call is resolved under the same key.
    """


class BlockState(Enum):
    IDLE = "idle"
    BLOCKED = "blocked"      # ceiling standing, nothing moved
    SETTLED = "settled"      # debited and the remainder released
    CAPTURED = "captured"    # captured once: the rail takes no further capture or void; what
                             # becomes of an uncaptured remainder is the rail's, and unobserved
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
    """What every rail must be able to do.

    `idempotency_key` is optional: a rail that sets `supports_idempotency = True`
    guarantees a repeated call with the same key acts once and returns the first
    result. The session relies on that to recover from a crash or a lost response
    by re-issuing the call. A rail that cannot make the guarantee must not claim
    it — recovery then requires reconciling against the rail's own state instead.
    """

    rail_id: str

    def reserve(self, ceiling: int, payee: str, *,
                idempotency_key: str | None = None) -> BlockRef: ...
    def debit(self, ref: BlockRef, amount: int, *,
              idempotency_key: str | None = None) -> BlockRef: ...
    def release(self, ref: BlockRef, amount: int | None = None, *,
                idempotency_key: str | None = None) -> BlockRef: ...
    def revoke(self, ref: BlockRef) -> BlockRef: ...
    def status(self, ref: BlockRef) -> BlockState: ...
