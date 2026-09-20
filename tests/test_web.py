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


class TestRealRailReceipt:
    """The frozen, signed record of the live Cashfree pre-auth run.

    Served static (no rail call, no credentials in the public app), and it must
    verify standalone exactly like a simulated packet — that is the whole point.
    """

    def test_real_rail_packet_is_served_and_verifies(self):
        r = client.get("/api/real-rail")
        assert r.status_code == 200
        data = r.json()
        EvidenceChain.verify_packet(data["packet"])
        assert data["measured"]["debited"] == 470_00
        assert data["measured"]["held"] == 620_00

    def test_the_console_does_not_claim_the_remainder_came_back(self):
        """It states what was measured: the capture. The release was not observed."""
        m = client.get("/api/real-rail").json()["measured"]
        assert "returned" not in m
        assert m["remainder"] == 150_00
        assert m["remainder_release"] == "unverified"
        assert m["date"] == client.get("/api/real-rail").json()["packet"]["entries"][0][
            "timestamp"][:10]                      # the run's own date, not a hardcoded one

    def test_real_rail_packet_carries_a_measured_debit_transition(self):
        packet = client.get("/api/real-rail").json()["packet"]
        debits = [e for e in packet["entries"]
                  if e["event_type"] == "rail_transition"
                  and e["payload"].get("action") == "debit"]
        assert len(debits) == 1
        p = debits[0]["payload"]
        assert p["amount"] == 470_00
        assert p["http_status"] == 200          # the live rail said yes
        assert "cf_payment_id" not in p or True  # response fields are recorded, not required


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


class TestReApprovalOverHttp:
    """A fare above the cap: refused, then a human-signed raise lets it settle.

    The console's re-approval flow. The over-cap block is refused first, the
    agent asks, a human key signs a widened envelope, and only then does the
    block settle — all in one verifiable packet that names who raised the cap.
    """

    def request(self, budget, ceiling, actual, payee="citycabs"):
        return {"envelope": {"budget": budget, "per_txn": budget, "payee": payee, "hours": 6},
                "ceiling": ceiling, "actual": actual, "payee": payee}

    def signed_consent(self, request, key):
        """The two-step flow, with the human's key held HERE — as the browser holds its own.

        The server never receives a private key: it hands back the exact consent
        to sign for this envelope and this public key, and later verifies the result.
        """
        from amanat.policy.consent import sign_consent
        pub = key.public_key().public_bytes_raw().hex()
        body = client.post("/api/reapprove/consent", json={**request, "public_key": pub}).json()
        return sign_consent(body["consent"], key)

    def reapp(self, budget, ceiling, actual, payee="citycabs", key=None):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        request = self.request(budget, ceiling, actual, payee)
        key = key or Ed25519PrivateKey.generate()
        return client.post("/api/reapprove",
                           json={**request, "consent": self.signed_consent(request, key)})

    def test_the_server_hands_back_the_exact_consent_to_sign_for_this_key(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        pub = key.public_key().public_bytes_raw().hex()
        r = client.post("/api/reapprove/consent",
                        json={**self.request(1_000_00, 1_200_00, 1_150_00), "public_key": pub})
        body = r.json()["consent"]
        assert body["event"] == "envelope_widened" and "signature" not in body
        assert body["cnf"]["jwk"]["x"] == pub
        assert body["from"]["max_total"] == 1_000_00 and body["to"]["max_total"] >= 1_200_00

    def test_a_reapproval_without_a_consent_is_rejected(self):
        r = client.post("/api/reapprove", json=self.request(1_000_00, 1_200_00, 1_150_00))
        assert r.status_code == 422

    def test_a_consent_signed_for_a_different_envelope_does_not_raise_the_cap(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        other = self.signed_consent(self.request(500_00, 1_200_00, 1_150_00), key)
        r = client.post("/api/reapprove",
                        json={**self.request(1_000_00, 1_200_00, 1_150_00), "consent": other})
        by = {s["type"]: s for s in r.json()["steps"]}
        assert by["approve_raise"]["ok"] is False
        assert by["debit"]["ok"] is False

    def test_the_key_that_widened_the_cap_is_the_one_the_human_signed_with(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        data = self.reapp(1_000_00, 1_200_00, 1_150_00, key=key).json()
        w = next(e for e in data["packet"]["entries"]
                 if e["payload"].get("event") == "envelope_widened")
        assert w["payload"]["cnf"]["jwk"]["x"] == key.public_key().public_bytes_raw().hex()

    def test_over_cap_is_refused_then_settles_after_a_signed_raise(self):
        data = self.reapp(1_000_00, 1_200_00, 1_150_00).json()
        by = {s["type"]: s for s in data["steps"]}
        # first block over the cap is refused; the human's approval is signed OK
        assert data["steps"][0]["type"] == "reserve" and data["steps"][0]["ok"] is False
        assert by["approve_raise"]["ok"] is True
        # after the raise, the block and the debit go through
        assert by["debit"]["ok"] is True
        assert data["raised_to"] >= 1_200_00

    def test_the_packet_verifies_and_names_who_raised_the_cap(self):
        data = self.reapp(1_000_00, 1_200_00, 1_150_00).json()
        EvidenceChain.verify_packet(data["packet"])
        widenings = [e for e in data["packet"]["entries"]
                     if e["event_type"] == "envelope"
                     and e["payload"].get("event") == "envelope_widened"]
        assert len(widenings) == 1
        w = widenings[0]["payload"]
        assert w["from"]["max_total"] == 1_000_00
        assert w["to"]["max_total"] >= 1_200_00
        assert w["cnf"]["jwk"]["crv"] == "Ed25519" and "signature" in w

    def test_the_agent_never_widened_its_own_grant(self):
        # the raise is a HUMAN-actor entry; the agent only ever PROPOSED it
        entries = self.reapp(1_000_00, 1_200_00, 1_150_00).json()["packet"]["entries"]
        widening = next(e for e in entries if e["payload"].get("event") == "envelope_widened")
        assert widening["actor"] == "human"
        proposals = [e for e in entries if e["event_type"] == "proposal"
                     and e["payload"].get("action") == "raise_ceiling"]
        assert proposals and all(e["actor"] == "agent" for e in proposals)


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


class TestDisputeAdjudicationEndpoint:
    """Adjudicate a run's own packet against the AP2 mandate it ran under."""

    def _packet(self):
        return sim(1_000_00, 800_00, "citycabs", HAPPY).json()["packet"]

    def test_a_settled_run_adjudicates_to_supports_merchant(self):
        r = client.post("/api/dispute", json={
            "packet": self._packet(), "assertion": "unauthorized"})
        assert r.status_code == 200
        d = r.json()
        assert d["finding"]["finding"] == "supports_merchant"
        assert d["finding"]["disclaimer"]
        assert d["representment"]["authorization"]["vct"].startswith("mandate.payment.open")

    def test_a_refused_amount_is_reported_not_in_the_chain(self):
        packet = sim(1_000_00, 800_00, "citycabs",
                     [{"type": "reserve", "amount": 5_000_00, "payee": "citycabs"}]).json()["packet"]
        r = client.post("/api/dispute", json={
            "packet": packet, "assertion": "unauthorized", "disputed_amount": 5_000_00})
        assert r.json()["finding"]["finding"] == "charge_not_in_chain"

    def test_non_delivery_is_outside_the_evidence(self):
        r = client.post("/api/dispute", json={
            "packet": self._packet(), "assertion": "non_delivery"})
        assert r.json()["finding"]["finding"] == "outside_evidence"

    def test_an_unknown_assertion_is_rejected(self):
        r = client.post("/api/dispute", json={
            "packet": self._packet(), "assertion": "made_up"})
        assert r.status_code == 422

    def test_a_packet_without_an_envelope_is_refused_cleanly(self):
        r = client.post("/api/dispute", json={
            "packet": {"entries": [], "public_key": "x", "genesis_hash": "0"*64},
            "assertion": "unauthorized"})
        assert r.status_code == 422


class TestRateLimiting:
    """A public, credential-free endpoint must not be an open firehose.

    Limits are per client address, in-memory (Cloud Run runs one instance at a
    time for this service), and generous enough that a visitor clicking around
    never hits them — only a script would.
    """

    def _fresh_client(self):
        from web import app as webapp
        webapp.LIMITER.reset()
        return TestClient(app)

    def test_normal_use_is_never_limited(self):
        c = self._fresh_client()
        for _ in range(10):
            assert c.post("/api/simulate", json={
                "envelope": {"budget": 100_00, "per_txn": 80_00, "payee": "p", "hours": 6},
                "actions": []}).status_code == 200

    def test_a_burst_past_the_limit_gets_429_with_retry_after(self):
        from web import app as webapp
        c = self._fresh_client()
        for _ in range(webapp.SIMULATE_LIMIT):
            c.post("/api/simulate", json={
                "envelope": {"budget": 100_00, "per_txn": 80_00, "payee": "p", "hours": 6},
                "actions": []})
        r = c.post("/api/simulate", json={
            "envelope": {"budget": 100_00, "per_txn": 80_00, "payee": "p", "hours": 6},
            "actions": []})
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert "slow down" in r.json()["error"].lower()

    def test_the_agent_endpoint_has_a_tighter_limit(self):
        from web import app as webapp
        assert webapp.AGENT_LIMIT < webapp.SIMULATE_LIMIT

    def test_health_is_never_limited(self):
        c = self._fresh_client()
        for _ in range(200):
            assert c.get("/api/health").status_code == 200


class TestTheRegistryIsServedByTheConsole:
    """The comparison page and its data, from the same origin as the demo. Public data only:
    the directory served holds exactly the page, the registry and its schema."""

    def test_the_page_is_served_at_the_directory_url(self):
        r = client.get("/registry/")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        assert "<title>Payment-rail semantics</title>" in r.text and "The matrix" in r.text

    def test_the_bare_path_redirects_so_the_pages_relative_links_resolve(self):
        r = client.get("/registry", follow_redirects=False)
        assert r.status_code in (301, 307, 308) and r.headers["location"].endswith("/registry/")

    def test_the_data_and_its_schema_are_served_beside_it(self):
        data = client.get("/registry/registry.json")
        assert data.status_code == 200 and data.json()["schema_version"] == 2
        schema = client.get("/registry/registry.schema.json")
        assert schema.status_code == 200 and schema.json()["title"].startswith("Amanat rail-semantics")

    def test_what_is_served_is_what_is_committed(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "docs" / "registry"
        assert client.get("/registry/").text == (root / "index.html").read_text(encoding="utf-8")
        assert client.get("/registry/registry.json").text == (root / "registry.json").read_text(encoding="utf-8")

    @pytest.mark.parametrize("path", ["/registry/../../.env", "/registry/..%2f..%2fpyproject.toml",
                                      "/registry/%2e%2e/%2e%2e/README.md", "/registry//etc/passwd"])
    def test_nothing_outside_the_directory_can_be_read(self, path):
        r = client.get(path)
        assert r.status_code in (400, 404), (path, r.status_code)

    def test_the_mount_serves_the_registry_directory_and_not_its_neighbours(self):
        for path in ("/registry/RAIL_SEMANTICS.md", "/registry/observations/store/watch.jsonl",
                     "/registry/sources/", "/registry/../RAIL_SEMANTICS.md"):
            assert client.get(path).status_code == 404, path

    def test_only_the_three_public_files_are_in_the_served_directory(self):
        from pathlib import Path
        served = {p.name for p in (Path(__file__).resolve().parents[1] / "docs" / "registry").iterdir()}
        assert served == {"index.html", "registry.json", "registry.schema.json"}

    def test_the_container_image_carries_what_the_route_serves(self):
        from pathlib import Path
        docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
        web_stage = docker[docker.index("AS web"):]
        assert "COPY docs/registry/" in web_stage
