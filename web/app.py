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

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from amanat.orchestrator.session import AgentSession
from amanat.policy.envelope import Envelope
from amanat.rails.simulator import SimulatedRail

MAX_PAISE = 100_000_00     # ₹1,00,000 — generous for a demo, bounds abuse
MAX_ACTIONS = 20

app = FastAPI(title="Amanat — governed-core demo", docs_url=None, redoc_url=None)

_PAGE = ""


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


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/simulate")
def simulate(body: SimulateIn) -> JSONResponse:
    return JSONResponse(run_simulation(body))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE


def _load_page() -> str:
    from amanat.evidence.render import _VERIFY_JS
    html = (Path(__file__).parent / "index.html").read_text()
    return html.replace("/*__VERIFY__*/", _VERIFY_JS)


_PAGE = _load_page()
