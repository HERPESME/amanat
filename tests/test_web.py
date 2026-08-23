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
