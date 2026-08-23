"""The demo's HTTP surface. Credential-free, no network — the governed core.

Confirms the endpoint drives the real policy engine (allows the happy path,
refuses over-budget / wrong-payee / ceiling-too-low), bounds its inputs, and
returns a packet that verifies.
"""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from web.app import app
from amanat.evidence.chain import EvidenceChain

client = TestClient(app)


def sim(budget, per_txn, payee, actions):
    return client.post("/api/simulate", json={
        "envelope": {"budget": budget, "per_txn": per_txn, "payee": payee, "hours": 6},
        "actions": actions,
    })


HAPPY = [
    {"type": "reserve", "amount": 620_00, "payee": "citycabs", "reason": "p95"},
    {"type": "debit", "amount": 470_00, "reason": "fare"},
    {"type": "release", "reason": "done"},
]


class TestTheGovernedCoreRunsBehindHttp:
    def test_happy_path_is_all_allowed_and_the_packet_verifies(self):
        r = sim(1_000_00, 800_00, "citycabs", HAPPY)
        assert r.status_code == 200
        data = r.json()
        assert [s["ok"] for s in data["steps"]] == [True, True, True]
        EvidenceChain.verify_packet(data["packet"])

    def test_over_budget_reserve_is_refused(self):
        data = sim(1_000_00, 800_00, "citycabs",
                   [{"type": "reserve", "amount": 5_000_00, "payee": "citycabs"}]).json()
        assert data["steps"][0]["ok"] is False
        assert "budget" in data["steps"][0]["detail"].lower()

    def test_wrong_payee_is_refused(self):
        data = sim(1_000_00, 800_00, "citycabs",
                   [{"type": "reserve", "amount": 100_00, "payee": "randomcab"}]).json()
        assert data["steps"][0]["ok"] is False

    def test_ceiling_too_low_refuses_the_debit(self):
        data = sim(1_000_00, 800_00, "citycabs", [
            {"type": "reserve", "amount": 380_00, "payee": "citycabs"},
            {"type": "debit", "amount": 470_00},
        ]).json()
        assert data["steps"][0]["ok"] is True
        assert data["steps"][1]["ok"] is False

    def test_refusals_appear_in_the_packet(self):
        data = sim(1_000_00, 800_00, "citycabs",
                   [{"type": "reserve", "amount": 5_000_00, "payee": "citycabs"}]).json()
        kinds = {e["event_type"] for e in data["packet"]["entries"]}
        assert "refusal" in kinds


class TestInputsAreBounded:
    def test_a_negative_amount_is_rejected(self):
        assert sim(1_000_00, 800_00, "citycabs",
                   [{"type": "reserve", "amount": -5}]).status_code == 422

    def test_an_absurd_amount_is_rejected(self):
        assert sim(1_000_00, 800_00, "citycabs",
                   [{"type": "reserve", "amount": 10**12}]).status_code == 422

    def test_an_unknown_action_type_is_rejected(self):
        assert sim(1_000_00, 800_00, "citycabs",
                   [{"type": "wire_transfer", "amount": 100}]).status_code == 422

    def test_too_many_actions_are_rejected(self):
        assert sim(1_000_00, 800_00, "citycabs",
                   [{"type": "reserve", "amount": 1} for _ in range(50)]).status_code == 422


class TestThePageIsServed:
    def test_health(self):
        assert client.get("/api/health").json() == {"ok": True}

    def test_index_serves_the_page_with_the_verifier_injected(self):
        html = client.get("/").text
        assert "Amanat" in html
        assert "crypto.subtle.verify" in html   # the verify JS was injected
        assert "/*__VERIFY__*/" not in html      # ...and the placeholder is gone


ENV = {"budget": 100_00 * 10, "per_txn": 80_000, "payee": "citycabs", "hours": 6}


class TestTheByoKeyAgentEndpoint:
    """The visitor's key drives a real agent, against the simulator.

    The key is used once and must never appear in a response or a raised error,
    and a hostile prompt can only ever produce refusals — there is no real rail.
    """

    def test_a_short_key_is_rejected(self):
        r = client.post("/api/agent", json={
            "gemini_key": "short", "prompt": "hi", "envelope": ENV})
        assert r.status_code == 422

    def test_an_over_long_prompt_is_rejected(self):
        r = client.post("/api/agent", json={
            "gemini_key": "x" * 30, "prompt": "a" * 3000, "envelope": ENV})
        assert r.status_code == 422

    def test_the_key_reaches_the_backend_and_never_the_response(self, monkeypatch):
        seen = {}

        class Fake:
            def run(self, session, prompt):
                session.reserve(620_00, "citycabs", "p95")
                session.debit(470_00, "fare")
                session.release(reason="done")
                return "Booked the cab; the merchant nets ₹470."

        monkeypatch.setattr("web.app._make_backend",
                            lambda key: (seen.__setitem__("key", key), Fake())[1])
        KEY = "AIzaFAKEfakefakefakefakefake"
        r = client.post("/api/agent", json={
            "gemini_key": KEY, "prompt": "book a cab", "envelope": ENV})
        assert r.status_code == 200
        assert seen["key"] == KEY          # passed through to the backend
        assert KEY not in r.text           # ...but never echoed back
        data = r.json()
        assert "Booked" in data["reply"]
        EvidenceChain.verify_packet(data["packet"])

    def test_a_failing_backend_leaks_neither_the_key_nor_the_stack(self, monkeypatch):
        class Boom:
            def run(self, *a):
                raise RuntimeError("secret internal detail")

        monkeypatch.setattr("web.app._make_backend", lambda key: Boom())
        KEY = "AIzaSECRETsecretsecretsecret"
        r = client.post("/api/agent", json={
            "gemini_key": KEY, "prompt": "x", "envelope": ENV})
        assert r.status_code == 502
        assert KEY not in r.text
        assert "secret internal detail" not in r.text
        assert "failed" in r.json()["error"].lower()
