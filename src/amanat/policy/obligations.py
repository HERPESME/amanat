"""Obligation clocks: a hold that outlives what it was for is noticed.

A ceiling placed on a rail is the customer's money, held. Three clocks can be running on it:

* the rail's own deadline, where the registry cites one (`hold_expiry_days`: Cashfree releases an
  authorisation not captured within seven days, Razorpay within three). After it the rail, not
  the agent, decides what becomes of the hold, and the chain should say what did;
* the deadline the human gave: what is not drawn must be released within so long of the last
  debit (`release_remainder_within`). It applies only where the registry does not say, on
  evidence usable as fact, that the rail returns the remainder by itself. A rail that does not, or
  whose behaviour is UNVERIFIED, leaves the release to the agent;
* the end of the human's authority (`resolve_by`, in a session the envelope's expiry): nothing
  may stay held past the grant that placed it.

When a clock passes with money still held, the hold is an orphan.

Pure: a function of a chain's entries and a time. It reads what the rail transitions say, moves no
money and calls no model. It reads a live chain's entries or an exported packet's dicts alike, so a
packet can be checked by someone who does not run the orchestrator. Time is always explicit and
timezone-aware; an unreadable or naive timestamp is an error, never a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from amanat.evidence.transitions import is_effective, transition_name
from amanat.rails.semantics import RAILS

PENDING, OVERDUE, MET = "pending", "overdue", "met"
RAIL_HOLD_EXPIRY = "rail_hold_expiry"
REMAINDER_RELEASE = "remainder_release"
RESOLVE_BY = "resolve_by"
_HOLD_ACTIONS = frozenset({"reserve", "debit", "release", "revoke"})


@dataclass(frozen=True)
class ObligationPolicy:
    """The deadlines a person chose, as opposed to the rail's own."""

    release_remainder_within: timedelta | None = None
    resolve_by: datetime | None = None


@dataclass(frozen=True)
class Hold:
    """What a chain says about one hold on a rail."""

    block_id: str
    placed_seq: int
    placed_at: datetime
    held: int
    debited: int = 0
    released: int = 0
    last_debit_at: datetime | None = None
    revoked: bool = False

    @property
    def remainder(self) -> int:
        return 0 if self.revoked else max(self.held - self.debited - self.released, 0)

    @property
    def closed(self) -> bool:
        return self.remainder == 0


@dataclass(frozen=True)
class Obligation:
    kind: str
    block_id: str
    placed_seq: int
    due_at: datetime
    status: str            # pending | overdue | met
    remainder: int
    basis: str             # why this clock exists
    citation: str = ""     # the registry's citation, when the clock is the rail's own
    url: str = ""
    quote: str = ""

    def to_payload(self, noticed_at: datetime) -> dict:
        """What is written into the chain when an overdue obligation is noticed."""
        return {"rule": "obligation_overdue", "kind": self.kind, "block_id": self.block_id,
                "placed_seq": self.placed_seq, "remainder": self.remainder,
                "due_at": self.due_at.isoformat(), "noticed_at": noticed_at.isoformat(),
                "basis": self.basis, "citation": self.citation, "url": self.url, "quote": self.quote}


def _moment(value: Any, what: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"unreadable {what}: {value!r}") from None
    if moment.tzinfo is None:
        raise ValueError(f"{what} {value!r} carries no timezone")
    return moment


def _plain(entry: Any, index: int) -> tuple[str, str, dict, int]:
    """(event type, timestamp, payload, seq) of a chain `Entry` or a packet's dict."""
    if isinstance(entry, dict):
        return entry["event_type"], entry["timestamp"], entry["payload"], entry.get("seq", index)
    return entry.event_type.value, entry.timestamp, entry.payload, entry.seq


def holds(entries: Iterable[Any]) -> list[Hold]:
    """The holds a chain's applied rail transitions add up to, in the order they were placed."""
    building: dict[str, dict] = {}
    for index, entry in enumerate(entries):
        kind, stamp, payload, seq = _plain(entry, index)
        if kind != "rail_transition" or not is_effective(payload):
            continue
        action, block = transition_name(payload), payload.get("block_id")
        if action not in _HOLD_ACTIONS or not block:
            continue
        when = _moment(stamp, "timestamp")
        if action == "reserve":
            building.setdefault(block, {"block_id": block, "placed_seq": seq, "placed_at": when,
                                        "held": payload["amount"], "debited": 0, "released": 0,
                                        "last_debit_at": None, "revoked": False})
        elif block in building:
            h = building[block]
            if action == "debit":
                h["debited"] += payload["amount"]
                h["last_debit_at"] = when
            elif action == "release":
                h["released"] += payload["amount"]
            else:
                h["revoked"] = True
    return [Hold(**h) for h in building.values()]


def _status(hold: Hold, due: datetime, now: datetime) -> str:
    if hold.closed:
        return MET
    return OVERDUE if now >= due else PENDING


def obligations(entries: Iterable[Any], *, rail_id: str, now: datetime,
                policy: ObligationPolicy = ObligationPolicy()) -> list[Obligation]:
    """Every clock running on every hold in `entries`, as of `now`."""
    if now.tzinfo is None:
        raise ValueError("now carries no timezone: a naive time would be read as local")
    rail = RAILS.get(rail_id)
    expiry = rail.limit("hold_expiry_days") if rail else None
    rail_frees_remainder = bool(rail and rail.permits("remainder_auto_released"))
    out: list[Obligation] = []
    for h in holds(entries):
        def make(kind, due, basis, **evidence):
            return Obligation(kind, h.block_id, h.placed_seq, due, _status(h, due, now),
                              h.remainder, basis, **evidence)

        if expiry is not None:
            out.append(make(
                RAIL_HOLD_EXPIRY, h.placed_at + timedelta(days=expiry.value),
                f"{rail.display_name}: a hold has a documented life of {expiry.render()}; after it the "
                "rail, not the agent, decides what becomes of the hold, and this record should say what did",
                citation=expiry.citation, url=expiry.url, quote=expiry.quote))
        if policy.release_remainder_within is not None and not rail_frees_remainder:
            start = h.last_debit_at or h.placed_at
            out.append(make(
                REMAINDER_RELEASE, start + policy.release_remainder_within,
                f"policy: the human asked for what is not drawn to be released within "
                f"{policy.release_remainder_within} of the last debit (or of placing the hold, "
                "if nothing has been drawn); this rail does not evidence returning it by itself"))
        if policy.resolve_by is not None:
            out.append(make(
                RESOLVE_BY, policy.resolve_by,
                f"the grant ends at {policy.resolve_by.isoformat()}: nothing may stay held past the "
                "authority that placed it"))
    return out


def overdue(found: Iterable[Obligation]) -> list[Obligation]:
    """The orphans, soonest deadline first."""
    return sorted((o for o in found if o.status == OVERDUE),
                  key=lambda o: (o.due_at, o.kind, o.block_id))
