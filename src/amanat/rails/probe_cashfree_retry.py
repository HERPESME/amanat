"""Confirm, on Cashfree's sandbox, that a retry after a lost response acts once.

    uv run --with httpx --with cryptography python -m amanat.rails.probe_cashfree_retry

The adapter claims that a call repeated under the same key acts once, and the session relies on that to
recover a call whose outcome is unknown. The probes (`python -m amanat.probes`) measured the rail's
answers one at a time. This asks the question the way a failure asks it: the real adapter makes the call,
the rail acts, and the *answer is lost* (a `ReadTimeout` raised after the rail replied), then the call is
repeated under the same key. What the rail holds afterwards is read back and counted.

Five scenarios, each on a fresh hold: a reservation whose answer is lost, a capture, a release, a whole
`AgentSession` that records the capture as in doubt and resolves it, and the same after a restart: the
process "dies", a new adapter that remembers nothing and the chain from disk rebuild the block from the
rail's own state and resolve the call. Each passes only if the rail ends up holding exactly one of the thing. Sandbox only, by the adapter's construction; the authorisation is forced
with `POST /simulate`, so this is Cashfree's sandbox API, not an issuer. Nothing is stored: this is a check
to run, not a measurement to keep (the measurements are the four `cashfree_preauth` retry rows).
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from amanat.rails.base import BlockState
from amanat.rails.cashfree import CashfreePreAuthRail


def _tag(method: str, path: str, body) -> str:
    if path.endswith("/authorization"):
        return str((body or {}).get("action", "")).lower()
    return {("POST", "/orders"): "create", ("POST", "/orders/sessions"): "pay",
            ("POST", "/simulate"): "simulate"}.get((method, path), f"{method} {path}")


class Lossy:
    """Mixin: the call is made and the rail replies, then the reply is lost, once, for a chosen call."""

    lose_after: set[str]

    def _call(self, method, path, **kw):
        out = super()._call(method, path, **kw)
        tag = _tag(method, path, kw.get("json"))
        if tag in getattr(self, "lose_after", ()):
            self.lose_after.discard(tag)
            raise httpx.ReadTimeout(f"the reply to {tag} was lost after the rail had acted")
        return out


def lossy(rail_class: type) -> type:
    return type("Lossy" + rail_class.__name__, (Lossy, rail_class), {})


RESERVATION = "a reservation whose answer is lost"
CAPTURE = "a capture whose answer is lost"
RELEASE = "a release whose answer is lost"
SESSION = "a whole session recovering from a lost capture"
RESTART = "a restarted session recovering from a lost capture"


@dataclass
class Outcome:
    name: str
    ok: bool
    detail: str


def _rail(factory: Callable[[], CashfreePreAuthRail]) -> CashfreePreAuthRail:
    rail = factory()
    rail.lose_after = set()
    return rail


def _key() -> str:
    return f"amanat-retry-{uuid.uuid4().hex[:16]}"


def _payments(rail, order_id: str) -> list[dict]:
    sc, body = rail.fetch_payments(order_id)
    return body if sc == 200 and isinstance(body, list) else []


def _lost(call) -> bool:
    try:
        call()
    except httpx.ReadTimeout:
        return True
    return False


def reservation(factory) -> Outcome:
    """The hold was placed and the answer lost: the retry reads it back and places no second hold."""
    rail, key = _rail(factory), _key()
    rail.lose_after.add("simulate")
    lost = _lost(lambda: rail.reserve(62_000, "probe", idempotency_key=key))
    ref = rail.reserve(62_000, "probe", idempotency_key=key)
    pays = _payments(rail, ref.block_id)
    ok = (lost and ref.state is BlockState.BLOCKED and len(pays) == 1
          and pays[0].get("payment_status") == "SUCCESS" and not pays[0].get("is_captured"))
    return Outcome(RESERVATION, ok,
                   f"lost={lost}, state={ref.state.value}, payments on the order={len(pays)}")


def capture(factory) -> Outcome:
    """The capture happened and the answer was lost: the retry returns the first result, one capture."""
    rail = _rail(factory)
    ref = rail.reserve(62_000, "probe", idempotency_key=_key())
    key = _key()
    rail.lose_after.add("capture")
    lost = _lost(lambda: rail.debit(ref, 47_000, idempotency_key=key))
    before = ref.debited
    rail.debit(ref, 47_000, idempotency_key=key)
    caps = [p for p in _payments(rail, ref.block_id) if (p.get("authorization") or {}).get("action") == "CAPTURE"]
    ok = lost and before == 0 and ref.debited == 47_000 and len(caps) == 1
    return Outcome(CAPTURE, ok,
                   f"lost={lost}, debited before the retry={before}, after={ref.debited}, captures on the rail={len(caps)}")


def release(factory) -> Outcome:
    """The void happened and the answer was lost: the retry is not refused as 'already voided'."""
    rail = _rail(factory)
    ref = rail.reserve(62_000, "probe", idempotency_key=_key())
    key = _key()
    rail.lose_after.add("void")
    lost = _lost(lambda: rail.release(ref, idempotency_key=key))
    rail.release(ref, idempotency_key=key)
    voids = [p for p in _payments(rail, ref.block_id) if (p.get("authorization") or {}).get("action") == "VOID"]
    ok = lost and ref.state is BlockState.REVOKED and ref.released == 62_000 and len(voids) == 1
    return Outcome(RELEASE, ok,
                   f"lost={lost}, state={ref.state.value}, voids on the rail={len(voids)}")


def session(factory) -> Outcome:
    """The whole path: the session records the lost capture as in doubt, stops moving money, resolves it."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from amanat.evidence.chain import EvidenceChain
    from amanat.orchestrator.session import AgentSession
    from amanat.policy.envelope import Envelope

    rail = _rail(factory)
    envelope = Envelope(subject="cab", max_total=100_000, max_per_txn=80_000, allowed_payees=["citycabs"],
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    s = AgentSession(envelope, rail, chain=EvidenceChain.with_key("cab", Ed25519PrivateKey.generate()))
    reserved = s.reserve(62_000, "citycabs", "ceiling").ok
    rail.lose_after.add("capture")
    lost = s.debit(47_000, "the fare")
    frozen = s.in_doubt is not None and not s.debit(1_000, "another").ok
    resolved = s.resolve_in_doubt().ok
    caps = [p for p in _payments(rail, s.block.block_id) if (p.get("authorization") or {}).get("action") == "CAPTURE"]
    s.chain.verify()
    ok = reserved and not lost.ok and frozen and resolved and s.state.debited == 47_000 and len(caps) == 1
    return Outcome(SESSION, ok,
                   f"in doubt then resolved={frozen and resolved}, ledger debited={s.state.debited}, "
                   f"captures on the rail={len(caps)}, chain verifies")


def restart(factory) -> Outcome:
    """The process dies after the rail acts: a new adapter and the chain on disk finish the job, once."""
    import tempfile
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from amanat.evidence.chain import EvidenceChain
    from amanat.orchestrator.session import AgentSession
    from amanat.policy.envelope import Envelope

    def envelope():
        return Envelope(subject="cab", max_total=100_000, max_per_txn=80_000, allowed_payees=["citycabs"],
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))

    with tempfile.TemporaryDirectory() as tmp:
        key, path = Ed25519PrivateKey.generate(), Path(tmp) / "chain.jsonl"
        first = _rail(factory)
        s = AgentSession(envelope(), first, chain=EvidenceChain.with_key("cab", key, store=path))
        reserved = s.reserve(62_000, "citycabs", "ceiling").ok
        first.lose_after.add("capture")
        lost = s.debit(47_000, "the fare")
        order_id = s.block.block_id
        # the process is gone: nothing survives but the chain on disk and the rail's own state
        s2 = AgentSession(envelope(), _rail(factory), resume=True, chain=EvidenceChain.load(path, key))
        in_doubt = s2.in_doubt is not None
        resolved = s2.resolve_in_doubt().ok
        caps = [p for p in _payments(s2.rail, order_id) if (p.get("authorization") or {}).get("action") == "CAPTURE"]
        s2.chain.verify()
        ok = reserved and not lost.ok and in_doubt and resolved and s2.state.debited == 47_000 and len(caps) == 1
    return Outcome(RESTART, ok,
                   f"in doubt after the restart={in_doubt}, resolved={resolved}, ledger debited={s2.state.debited}, "
                   f"captures on the rail={len(caps)}, chain verifies")


SCENARIOS = ((RESERVATION, reservation), (CAPTURE, capture), (RELEASE, release), (SESSION, session),
             (RESTART, restart))


def run(factory) -> list[Outcome]:
    """Every scenario. One that raises is a failure with its error, never a crash of the whole check."""
    outcomes = []
    for title, scenario in SCENARIOS:
        try:
            outcomes.append(scenario(factory))
        except Exception as exc:  # noqa: BLE001 — the point of the check is to find what breaks
            outcomes.append(Outcome(title, False, f"{type(exc).__name__}: {exc}"))
    return outcomes


def main(argv: list[str] | None = None, *, factory=None, out=print) -> int:
    if factory is None:
        from amanat import env
        env.load()
        LossyCashfree = lossy(CashfreePreAuthRail)
        factory = LossyCashfree
    out("A retry after a lost response, on the sandbox (the authorisation is forced with POST /simulate):\n")
    results = run(factory)
    for r in results:
        out(f"  {'PASS' if r.ok else 'FAIL'}  {r.name}\n        {r.detail}")
    passed = all(r.ok for r in results)
    out("\n  " + ("every retry acted once" if passed else "a retry did not act once: do not rely on idempotency"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
