"""The LLM seam, and the .env loader.

The point of these tests is that adding a second provider did not add a second
way to reach money. Both backends dispatch through the same governed session, so
governance is a property of the architecture rather than of one provider's SDK.

No network, no API key.
"""
import os

import pytest

from amanat.env import load as load_env
from amanat.orchestrator import backends
from amanat.orchestrator.session import AgentSession
from amanat.rails.simulator import SimulatedRail


@pytest.fixture
def session(envelope, verified_rail):
    return AgentSession(envelope, SimulatedRail(verified_rail,
                                                customer_balance=10_000_00))


class TestDispatchIsGoverned:
    """Every backend reaches the rail through dispatch(), and only through it."""

    def test_reserve_within_envelope_is_applied(self, session):
        out = backends.dispatch(session, "reserve_funds", {
            "amount_paise": 620_00, "payee": "citycabs", "reason": "p95"})
        assert out.startswith("OK")
        assert session.state.blocked == 620_00

    def test_overspend_comes_back_as_a_refusal_not_an_exception(self, session):
        out = backends.dispatch(session, "reserve_funds", {
            "amount_paise": 5_000_00, "payee": "citycabs", "reason": "greedy"})
        assert out.startswith("REFUSED")
        assert session.state.blocked == 0

    def test_a_model_inventing_a_tool_gets_refused(self, session):
        assert backends.dispatch(session, "wire_transfer", {}).startswith("REFUSED")

    def test_refusals_reach_the_evidence_chain_via_dispatch(self, session):
        backends.dispatch(session, "reserve_funds", {
            "amount_paise": 9_999_00, "payee": "citycabs", "reason": "x"})
        assert len(session.chain.refusals()) == 1

    def test_dispatch_is_the_only_route_to_the_session(self):
        """If a backend grew its own rail access, this is what would catch it."""
        import inspect
        src = inspect.getsource(backends)
        # Backends may name session methods only inside dispatch().
        body = src.split("def dispatch(")[1].split("\n@runtime_checkable")[0]
        for method in ("session.reserve(", "session.debit(", "session.release("):
            assert src.count(method) == body.count(method), (
                f"{method} appears outside dispatch() — that is a bypass")


class TestBackendSelection:
    def test_returns_none_when_nothing_is_configured(self, monkeypatch):
        for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        assert backends.resolve_backend() is None

    def test_gemini_is_selected_when_its_key_is_present(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert backends.resolve_backend().name == "gemini"

    def test_anthropic_is_selected_when_only_its_key_is_present(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        assert backends.resolve_backend().name == "anthropic"

    def test_both_backends_satisfy_the_protocol(self):
        assert isinstance(backends.GeminiBackend(), backends.LLMBackend)
        assert isinstance(backends.AnthropicBackend(), backends.LLMBackend)

    def test_agent_run_without_a_credential_says_what_to_do(self, session, monkeypatch):
        from amanat.orchestrator import agent
        for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            agent.run(session, "book a cab")


class TestEnvLoader:
    def test_missing_file_is_not_an_error(self, tmp_path):
        assert load_env(tmp_path / "nope.env") == []

    def test_parses_comments_blanks_quotes_and_export(self, tmp_path, monkeypatch):
        monkeypatch.delenv("A", raising=False)
        monkeypatch.delenv("B", raising=False)
        monkeypatch.delenv("C", raising=False)
        f = tmp_path / ".env"
        f.write_text('# a comment\n\nA=plain\nB="quoted"\nexport C=exported\nnot_a_pair\n')
        assert sorted(load_env(f)) == ["A", "B", "C"]
        assert os.environ["A"] == "plain"
        assert os.environ["B"] == "quoted"
        assert os.environ["C"] == "exported"

    def test_real_environment_wins_over_the_file(self, tmp_path, monkeypatch):
        """A stale .env must never shadow an explicitly exported credential."""
        monkeypatch.setenv("KEY", "from-environment")
        f = tmp_path / ".env"
        f.write_text("KEY=from-file\n")
        assert load_env(f) == []
        assert os.environ["KEY"] == "from-environment"


class TestTheModelIsToldItsConstraints:
    """An agent that cannot see its envelope will flail against it.

    The first live run proved this: the model invented three payees, was
    refused three times, and told the user the envelope had no allocation —
    none of which it could have known was wrong.

    Showing the boundary is not the same as trusting the model to respect it.
    Policy still checks every call; this only removes the guessing.
    """

    def test_briefing_names_the_allowed_payees(self, session):
        assert "citycabs" in session.briefing()

    def test_briefing_states_the_budget_in_paise_and_rupees(self, session):
        b = session.briefing()
        assert str(session.envelope.max_total) in b
        assert "₹" in b

    def test_briefing_warns_when_the_rail_cannot_release_partially(self, session):
        """The teardown cost is a planning fact, so the model must know it."""
        assert "tears the whole block down" in session.briefing()

    def test_briefing_carries_the_rail_block_cap_when_one_is_declared(
            self, envelope, verified_rail):
        from amanat.orchestrator.session import AgentSession
        from amanat.rails.semantics import RAILS, Limit, SourceTier
        from amanat.rails.simulator import SimulatedRail

        RAILS[verified_rail].limits["max_block_amount"] = Limit(
            name="max_block_amount", value=10_000_00, unit="paise",
            source_tier=SourceTier.PRIMARY, citation="fixture",
            quote="cap", url="https://example.test")
        s = AgentSession(envelope, SimulatedRail(verified_rail))
        assert "₹10,000" in s.briefing()
