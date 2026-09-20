"""Reading what the money did out of a chain's rail transitions.

A rail transition entry records an *attempt* as well as a result: the session
writes one when the rail rejects a call, with `outcome: "rail_rejected"`, so that
a failed debit is evidence too. A consumer that sums debits by name alone would
report a rejected debit as money charged. Both consumers of a chain — the dispute
adjudicator and the reconciler — go through here, so they cannot disagree.

Fail closed: a transition counts as money moved only when it says so. An entry
with no `outcome` (the capture/refund vocabulary names the transition itself) or
`outcome: "applied"` counts; anything else — rejected, failed, unknown, or an
in-doubt attempt — does not.
"""
from __future__ import annotations

DEBIT_LIKE = frozenset({"debit", "debited", "captured"})
REFUND_LIKE = frozenset({"refunded"})


def transition_name(payload: dict) -> str:
    return str(payload.get("action") or payload.get("transition") or "").lower()


def is_effective(payload: dict) -> bool:
    """True if this transition actually moved money (or state) on the rail."""
    outcome = payload.get("outcome")
    return outcome is None or outcome == "applied"


def unresolved_in_doubt(entries: list[dict]) -> list[dict]:
    """`rail_in_doubt` entries whose call never reached a recorded outcome.

    A call in doubt may or may not have moved money. Until a later transition
    settles the same proposal, the record cannot say it did not.
    """
    settled = {e["payload"].get("proposal_seq") for e in entries
               if e["event_type"] == "rail_transition"}
    return [e for e in entries if e["event_type"] == "rail_in_doubt"
            and e["payload"].get("proposal_seq") not in settled]
