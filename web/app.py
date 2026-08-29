"""The demo's HTTP surface. FastAPI, one real endpoint, no credentials.

`/api/simulate` takes an envelope and a list of proposed actions, runs them
through the *real* governed core — the same `AgentSession`, `PolicyEngine` and
`EvidenceChain` the tests exercise — and returns each verdict plus the signed
evidence packet. The page then verifies that packet in the browser.

Inputs are bounded (positive integer paise, capped counts and sizes) so a public
visitor cannot turn the endpoint into anything but a governance demo. There is no
model call and no rail call that touches money.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.simulator import SimulatedRail

MAX_PAISE = 100_000_00     # ₹1,00,000 — generous for a demo, bounds abuse
MAX_ACTIONS = 20

app = FastAPI(title="Amanat — governed-core demo", docs_url=None, redoc_url=None)

_PAGE = ""

# Per-client rate limits, sliding window, in memory. Generous enough that a
# person clicking around never meets them; only a script does. In-memory is
# correct here because this service runs one instance at a time and holds no
# state worth coordinating — the point is to keep a public, credential-free
# surface from being an open firehose, not to meter a product.
SIMULATE_LIMIT = 60      # requests per window, /api/simulate and /api/dispute
AGENT_LIMIT = 10         # /api/agent — each one is a live model run on the caller's key
WINDOW_SECONDS = 60


class _Limiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float = WINDOW_SECONDS
              ) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(window - (now - q[0])) + 1)
            q.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITER = _Limiter()


def _limited(request: Request, bucket: str, limit: int) -> JSONResponse | None:
    """A 429 response if this client is over the limit for `bucket`, else None."""
    host = request.client.host if request.client else "unknown"
    ok, retry = LIMITER.check(f"{bucket}:{host}", limit)
    if ok:
        return None
    return JSONResponse(
        {"error": f"slow down — this endpoint allows {limit} requests per "
                  f"{WINDOW_SECONDS}s per client; try again in {retry}s"},
        status_code=429, headers={"Retry-After": str(retry)})


class Action(BaseModel):
    type: str = Field(pattern="^(reserve|debit|release)$")
    amount: int = Field(default=0, ge=0, le=MAX_PAISE)
    payee: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=200)


class EnvelopeIn(BaseModel):
    budget: int = Field(ge=1, le=MAX_PAISE)
    per_txn: int = Field(ge=1, le=MAX_PAISE)
    payee: str = Field(min_length=1, max_length=64)
    hours: int = Field(default=6, ge=1, le=72)


class SimulateIn(BaseModel):
    envelope: EnvelopeIn
    actions: list[Action] = Field(max_length=MAX_ACTIONS)


class AgentIn(BaseModel):
    # The visitor's own Gemini key — used for this one request, never stored,
    # never logged. Runs the real agent loop against the simulator, so the model
    # can propose anything and the governed core still bounds what happens.
    gemini_key: str = Field(min_length=20, max_length=200)
    prompt: str = Field(min_length=1, max_length=2000)
    envelope: EnvelopeIn


def run_simulation(body: SimulateIn) -> dict:
    """Drive the real governed core. Pure function of the request."""
    env = Envelope(
        subject="demo",
        max_total=body.envelope.budget,
        max_per_txn=body.envelope.per_txn,
        allowed_payees=[body.envelope.payee],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=body.envelope.hours),
        intent_text="interactive demo session",
    )
    session = AgentSession(env, SimulatedRail("sbmd", customer_balance=MAX_PAISE))

    steps = []
    for a in body.actions:
        if a.type == "reserve":
            r = session.reserve(a.amount, a.payee or body.envelope.payee, a.reason)
        elif a.type == "debit":
            r = session.debit(a.amount, a.reason)
        else:
            r = session.release(None, a.reason)
        steps.append({
            "type": a.type, "amount": a.amount,
            "payee": a.payee or body.envelope.payee, "reason": a.reason,
            "ok": r.ok, "detail": r.detail, "citation": r.citation,
            "state": r.state,
        })

    return {"steps": steps, "summary": session.summary(),
            "packet": session.evidence_packet()}


class DisputeIn(BaseModel):
    packet: dict
    assertion: str = Field(pattern="^(unauthorized|amount|wrong_payee|non_delivery)$")
    disputed_amount: int | None = Field(default=None, ge=0, le=MAX_PAISE)


def _envelope_from_packet(packet: dict) -> Envelope:
    """Rebuild the authorizing envelope from the packet's own signed record.

    The envelope is entry one of any chain, so a dispute can be adjudicated
    against the exact grant the settlement ran under, with nothing to trust
    beyond the signed packet itself.
    """
    from datetime import datetime as _dt

    entry = next((e for e in packet.get("entries", [])
                  if e.get("event_type") == "envelope"), None)
    if entry is None:
        raise ValueError("packet carries no envelope entry to adjudicate against")
    p = entry["payload"]
    return Envelope(
        subject=p.get("subject", "adjudicated"),
        max_total=int(p["max_total"]), max_per_txn=int(p["max_per_txn"]),
        allowed_payees=list(p["allowed_payees"]),
        expires_at=_dt.fromisoformat(p["expires_at"]),
        intent_text=p.get("intent_text", ""))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


def _load_real_rail() -> dict | None:
    """The frozen, signed record of a real Cashfree pre-auth run.

    Generated offline with sandbox credentials by
    `python -m amanat.rails.cashfree_settle` and committed. The public demo never
    calls the rail — it serves this static proof, which verifies standalone in the
    visitor's browser exactly like a simulated one.
    """
    path = Path(__file__).parent / "real_rail_packet.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


_REAL_RAIL = _load_real_rail()


@app.get("/api/real-rail")
def real_rail() -> JSONResponse:
    """The signed evidence packet from the live Cashfree pre-auth settlement."""
    if _REAL_RAIL is None:
        return JSONResponse({"error": "real-rail packet unavailable"}, status_code=404)
    return JSONResponse({
        "packet": _REAL_RAIL,
        "measured": {
            "rail": "Cashfree UPI pre-authorization (sandbox)",
            "held": 620_00, "debited": 470_00, "returned": 150_00,
            "http": 200, "date": "2026-08-29",
        },
    })


@app.post("/api/dispute")
def dispute(body: DisputeIn, request: Request) -> JSONResponse:
    """Adjudicate a settlement chain against the AP2 mandate it ran under.

    The finding says what the signed evidence shows — never who wins the dispute.
    """
    if (limited := _limited(request, "dispute", SIMULATE_LIMIT)) is not None:
        return limited
    from amanat.dispute.adjudicate import adjudicate, export_representment_packet
    from amanat.interop.ap2 import to_open_payment_mandate

    try:
        env = _envelope_from_packet(body.packet)
    except (ValueError, KeyError, TypeError):
        return JSONResponse(
            {"error": "this packet cannot be adjudicated — it has no envelope."},
            status_code=422)

    mandate = to_open_payment_mandate(env)
    adj = adjudicate(body.packet, mandate, body.assertion,
                     disputed_amount=body.disputed_amount)
    return JSONResponse({
        "finding": adj.to_dict(),
        "representment": export_representment_packet(adj, body.packet, mandate),
    })


@app.post("/api/simulate")
def simulate(body: SimulateIn, request: Request) -> JSONResponse:
    if (limited := _limited(request, "simulate", SIMULATE_LIMIT)) is not None:
        return limited
    return JSONResponse(run_simulation(body))


class ReapproveIn(BaseModel):
    envelope: EnvelopeIn
    ceiling: int = Field(ge=1, le=MAX_PAISE)
    actual: int = Field(ge=1, le=MAX_PAISE)
    payee: str = Field(default="", max_length=64)


@app.post("/api/reapprove")
def reapprove(body: ReapproveIn, request: Request) -> JSONResponse:
    """Re-approval: a fare above the cap, then the human raises it and signs.

    The agent's over-cap block is refused, the agent asks, a human key signs a
    widened envelope, and only then does the block go through and settle. The
    signed re-consent lands in the same chain, so the receipt shows who raised
    the cap and by how much — the agent never widened its own grant.
    """
    if (limited := _limited(request, "simulate", SIMULATE_LIMIT)) is not None:
        return limited
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    env = Envelope(
        subject="demo", max_total=body.envelope.budget,
        max_per_txn=body.envelope.per_txn, allowed_payees=[body.envelope.payee],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=body.envelope.hours),
        intent_text="re-approval demo")
    session = AgentSession(env, SimulatedRail("sbmd", customer_balance=MAX_PAISE))
    payee = body.payee or body.envelope.payee
    raise_to = max(body.ceiling, body.envelope.budget + 1)

    steps = []
    def rec(kind, amount, r):
        steps.append({"type": kind, "amount": amount, "ok": r.ok, "detail": r.detail})

    rec("reserve", body.ceiling, session.reserve(body.ceiling, payee, "block the fare"))
    session.propose_raise(raise_to, reason="the metered fare is above the current cap")
    steps.append({"type": "propose_raise", "amount": raise_to, "ok": False,
                  "detail": "agent asks the human to raise the cap"})
    ok = session.approve_raise(raise_to, Ed25519PrivateKey.generate(),
                               new_max_per_txn=raise_to, reason="rider approved the higher fare")
    steps.append({"type": "approve_raise", "amount": raise_to, "ok": ok.ok, "detail": ok.detail})
    rec("reserve", body.ceiling, session.reserve(body.ceiling, payee, "block at the raised cap"))
    rec("debit", body.actual, session.debit(body.actual, "metered fare"))
    rec("release", 0, session.release(reason="trip complete"))

    return JSONResponse({"steps": steps, "summary": session.summary(),
                         "packet": session.evidence_packet(), "raised_to": raise_to})


def _make_backend(api_key: str):
    """Overridable in tests so the endpoint can be exercised without a real key."""
    from amanat.orchestrator.backends import GeminiBackend
    return GeminiBackend(api_key=api_key)


@app.post("/api/agent")
def agent(body: AgentIn, request: Request) -> JSONResponse:
    """Run the real LLM agent on the visitor's key, against the simulator.

    The model proposes; the same policy engine and evidence chain govern what
    actually happens, so a hostile prompt can only produce refusals — never a
    real payment (there is no real rail here) and never a charge on our account
    (the key is the visitor's). The key is read from the body, used once, and
    never stored or logged.
    """
    if (limited := _limited(request, "agent", AGENT_LIMIT)) is not None:
        return limited
    env = Envelope(
        subject="agent-demo",
        max_total=body.envelope.budget,
        max_per_txn=body.envelope.per_txn,
        allowed_payees=[body.envelope.payee],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=body.envelope.hours),
        intent_text=body.prompt,
    )
    session = AgentSession(env, SimulatedRail("sbmd", customer_balance=MAX_PAISE))

    try:
        reply = _make_backend(body.gemini_key).run(session, body.prompt)
    except Exception as exc:                              # noqa: BLE001
        # Never surface the key or a raw stack trace. The class name is enough
        # to tell "bad key" from "network" without leaking anything.
        return JSONResponse(
            {"error": f"the agent run failed ({type(exc).__name__}). "
                      "Check the key is a valid Gemini API key and try again."},
            status_code=502)

    return JSONResponse({
        "reply": reply,
        "summary": session.summary(),
        "packet": session.evidence_packet(),
    })


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE


def _load_page() -> str:
    from amanat.evidence.render import _VERIFY_JS
    html = (Path(__file__).parent / "index.html").read_text()
    return html.replace("/*__VERIFY__*/", _VERIFY_JS)


_PAGE = _load_page()
