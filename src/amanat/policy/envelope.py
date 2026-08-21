"""The constraint envelope — what the human actually authorized.

Deliberately reuses AP2's vocabulary (`amount_range`, `budget`, `allowed_payees`,
`execution_date`) rather than inventing a parallel one. Two reasons, both
defensive:

  * AP2 already solved the *permission* half of this problem, and its
    `open_payment_mandate.json` schema is public. Reinventing it would be worse
    engineering and an easy question to lose in an interview.
  * It makes the actual contribution legible by contrast: AP2 says what the
    agent MAY spend; this project records what the money DID.

All amounts are integer paise. There is no float in this codebase that touches
money.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Envelope:
    """A bounded grant of spending authority, compiled from human intent.

    Frozen on purpose: an envelope is a record of what was authorized. Widening
    authority means issuing a new envelope, which leaves a trace, rather than
    mutating one, which does not.
    """

    subject: str
    max_total: int                    # paise, across the whole envelope
    max_per_txn: int                  # paise, any single transaction
    allowed_payees: list[str]
    expires_at: datetime
    intent_text: str = ""             # the human's own words, kept verbatim
    notes: str = ""

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def permits_payee(self, payee: str) -> bool:
        return payee in self.allowed_payees

    def to_payload(self) -> dict:
        """Serializable form for the evidence chain."""
        return {
            "subject": self.subject,
            "max_total": self.max_total,
            "max_per_txn": self.max_per_txn,
            "allowed_payees": list(self.allowed_payees),
            "expires_at": self.expires_at.isoformat(),
            "intent_text": self.intent_text,
        }


@dataclass
class LedgerState:
    """Where the money currently is for one subject.

    `blocked` is the ceiling standing against the rail. `debited` is what has
    actually moved. `released` is what went back. The amount-contingent
    invariant is: debited + released <= blocked.
    """

    blocked: int = 0
    debited: int = 0
    released: int = 0
    history: list[str] = field(default_factory=list)

    @property
    def available(self) -> int:
        """Headroom left inside the block."""
        return self.blocked - self.debited - self.released

    @property
    def stranded(self) -> int:
        """Customer money held but not yet debited or released.

        This is the cost side of setting the ceiling too high, and the number
        the ceiling model is optimised against.
        """
        return self.available
