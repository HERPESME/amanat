"""Obligation clocks: a hold that outlives what it was for is noticed.

A ceiling placed on a rail is the customer's money, held. Three clocks can be running on it:

* the rail's own deadline, where the registry cites one (`hold_expiry_days`: Cashfree releases an
  authorisation not captured within seven days, Razorpay within at most three; on Reserve Pay
  `max_block_validity_days`, or the end date the block itself reports). The rail acts at it, so the
  chain should say what became of the hold;
* the deadline the human gave: what is not drawn must be released within so long of the last
  debit (`release_remainder_within`), and, where the rail permits many debits, within so long of
  placing the hold whatever was drawn since (`release_remainder_absolute`): each debit restarts the
  first, so on a standing pool used every few days it would postpone the notice for ever. It applies
  only where the registry does not say, on evidence usable as fact, that the rail returns the
  remainder by itself;
* the end of the human's authority (`resolve_by`, in a session the envelope's expiry): nothing
  may stay held past the grant that placed it.

When a clock passes with money still shown held, the hold is either **overdue** or **unresolved**,
and the difference is the point. The remainder is arithmetic over the transitions the chain
recorded (held, less debited, less released); the rail was never asked. Where the registry says on
evidence usable as fact that the rail keeps the remainder (Reserve Pay), the chain's number is a
fair statement and the deadline is *overdue*. Where the rail itself acts at the deadline, or may
already have returned the money (its release is UNVERIFIED, or the registry has no row), a signed
"still held" would be a guess, so the deadline is *unresolved*: it passed, the chain shows no
release, and nobody has confirmed either way. The rule is the project's own, applied here too:
absence of evidence is not an assertion.

`met` means the release was instructed and the rail applied it. It does not mean the money has
reached the customer: Razorpay's auto-refund takes five to seven working days
(`auto_refund_speed_working_days_max`), and this module does not yet run that clock.

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

PENDING, OVERDUE, UNRESOLVED, MET = "pending", "overdue", "unresolved", "met"
RAIL_HOLD_EXPIRY = "rail_hold_expiry"
REMAINDER_RELEASE = "remainder_release"
RESOLVE_BY = "resolve_by"
_HOLD_ACTIONS = frozenset({"reserve", "debit", "release", "revoke"})
REMAINDER_BASIS = ("chain arithmetic over applied rail transitions (held, less debited, less released); "
                   "the rail was not asked")


@dataclass(frozen=True)
class ObligationPolicy:
    """The deadlines a person chose, as opposed to the rail's own."""

    release_remainder_within: timedelta | None = None       # idle: since the last debit
    resolve_by: datetime | None = None
    release_remainder_absolute: timedelta | None = None     # ceiling: since placement, whatever was drawn since


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
    restated: bool = False              # a later reserve named a different amount for this block
    ends_at: datetime | None = None     # the block's own end date, when the rail reported one

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
    status: str            # pending | overdue | unresolved | met
    remainder: int         # chain arithmetic, not the rail's word
    basis: str             # why this clock exists
    citation: str = ""     # the registry's citation, when the clock is the rail's own
    url: str = ""
    quote: str = ""
    rail_remainder_release: str = ""   # the tier of the registry's `remainder_auto_released` row, or "absent"

    def to_payload(self, noticed_at: datetime) -> dict:
        """What is written into the chain when a passed deadline is noticed.

        `obligation_overdue` says the registry evidences that the rail keeps the remainder;
        `obligation_unresolved` says the deadline passed and the chain shows no release, and does not
        say the money is still held. Either way the entry says what its number is and how well the
        registry knows what the rail does with a remainder.
        """
        if self.status not in (OVERDUE, UNRESOLVED):
            raise ValueError(f"only a deadline that has passed is written into the chain; this one is {self.status}")
        return {"rule": "obligation_overdue" if self.status == OVERDUE else "obligation_unresolved",
                "kind": self.kind, "block_id": self.block_id,
                "placed_seq": self.placed_seq, "remainder": self.remainder,
                "remainder_basis": REMAINDER_BASIS, "rail_remainder_release": self.rail_remainder_release,
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
    """The holds a chain's applied rail transitions add up to, in the order they were placed.

    A reserve repeated for a block with the same amount is a replayed record, one hold. A reserve
    that names a different amount for a live block is a modification or a mistake: the hold keeps
    the first amount and is marked `restated`, so nothing computed from it is taken for certain.
    """
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
            if block not in building:
                building[block] = {"block_id": block, "placed_seq": seq, "placed_at": when,
                                   "held": payload["amount"], "debited": 0, "released": 0,
                                   "last_debit_at": None, "revoked": False, "restated": False,
                                   "ends_at": _moment(payload["expires_at"], "expires_at")
                                   if payload.get("expires_at") is not None else None}
            elif payload["amount"] != building[block]["held"]:
                building[block]["restated"] = True
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


def _status(hold: Hold, due: datetime, now: datetime, *, rail_may_act: bool) -> str:
    """met, pending, or a passed deadline: overdue where the chain's claim is fair, unresolved where
    the rail may have acted (or the chain cannot say what the money is)."""
    if hold.closed:
        return MET
    if now < due:
        return PENDING
    return UNRESOLVED if rail_may_act or hold.restated else OVERDUE


def obligations(entries: Iterable[Any], *, rail_id: str, now: datetime,
                policy: ObligationPolicy = ObligationPolicy()) -> list[Obligation]:
    """Every clock running on every hold in `entries`, as of `now`."""
    if now.tzinfo is None:
        raise ValueError("now carries no timezone: a naive time would be read as local")
    rail = RAILS.get(rail_id)
    life = (rail.limit("hold_expiry_days") or rail.limit("max_block_validity_days")) if rail else None
    release = rail.capabilities.get("remainder_auto_released") if rail else None
    release_tier = release.source_tier.value if release else "absent"
    rail_frees_remainder = bool(rail and rail.permits("remainder_auto_released"))
    rail_keeps_remainder = bool(release and release.is_fact and release.supported is False)
    many_debits = bool(rail and rail.permits("multi_debit"))
    idle, ceiling = policy.release_remainder_within, policy.release_remainder_absolute
    out: list[Obligation] = []
    for h in holds(entries):
        restated = ("" if not h.restated else
                    " The chain shows a later reserve naming a different amount for this block (restated), so the "
                    "remainder cannot be computed from the first ceiling.")

        def make(kind, due, basis, *, rail_may_act, **evidence):
            return Obligation(kind, h.block_id, h.placed_seq, due,
                              _status(h, due, now, rail_may_act=rail_may_act),
                              h.remainder, basis + restated, rail_remainder_release=release_tier, **evidence)

        if h.ends_at is not None or life is not None:
            if h.ends_at is not None:
                due = h.ends_at
                why = "the block's own end date, as the rail reported it"
            elif life.name == "max_block_validity_days":
                due = h.placed_at + timedelta(days=life.value)
                why = (f"no end date was reported for this block, so the clock is the regulatory maximum for a block, "
                       f"up to {life.render()}, not this block's own end date")
            else:
                due = h.placed_at + timedelta(days=life.value)
                why = f"the registry documents a hold life of up to {life.render()}"
            cited = ({"citation": life.citation, "url": life.url, "quote": life.quote} if life is not None else {})
            out.append(make(
                RAIL_HOLD_EXPIRY, due,
                f"{rail.display_name if rail else rail_id}: {why}; the rail acts at that deadline, so this record "
                "should say what became of the hold", rail_may_act=True, **cited))
        if (idle is not None or ceiling is not None) and not rail_frees_remainder:
            bounds = []
            if idle is not None:
                bounds.append(((h.last_debit_at or h.placed_at) + idle, f"{idle} of the last debit (or of placing the "
                                                                        "hold, if nothing has been drawn)"))
            if ceiling is not None:
                bounds.append((h.placed_at + ceiling, f"{ceiling} of placing the hold, whatever has been drawn since"))
            due, within = min(bounds)
            postponed = (" This rail permits many debits and each one restarts this clock, so ordinary use of the "
                         "block can postpone this notice; set release_remainder_absolute to bound it."
                         if many_debits and ceiling is None else "")
            out.append(make(
                REMAINDER_RELEASE, due,
                f"policy: the human asked for what is not drawn to be released within {within}; this rail does not "
                f"evidence returning it by itself.{postponed}", rail_may_act=not rail_keeps_remainder))
        if policy.resolve_by is not None:
            out.append(make(
                RESOLVE_BY, policy.resolve_by,
                f"the grant ends at {policy.resolve_by.isoformat()}: nothing may stay held past the "
                "authority that placed it", rail_may_act=not rail_keeps_remainder))
    return out


def _soonest(found: Iterable[Obligation], statuses: tuple[str, ...]) -> list[Obligation]:
    return sorted((o for o in found if o.status in statuses), key=lambda o: (o.due_at, o.kind, o.block_id))


def overdue(found: Iterable[Obligation]) -> list[Obligation]:
    """The deadlines that passed with money the registry evidences the rail keeps, soonest first."""
    return _soonest(found, (OVERDUE,))


def orphans(found: Iterable[Obligation]) -> list[Obligation]:
    """Every deadline that passed with money still shown held, overdue or unresolved, soonest first."""
    return _soonest(found, (OVERDUE, UNRESOLVED))
