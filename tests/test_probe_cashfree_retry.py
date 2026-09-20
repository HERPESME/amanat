"""The retry check runs the same scenarios against any rail factory; here, against the fake sandbox.

The live run is a command a person makes against Cashfree's sandbox. What is tested here is that the check
is honest: it passes when the rail behaves as measured, and it fails, with the rail's own words, when a
rail does not honour a key, instead of crashing or passing.
"""
from amanat.rails import probe_cashfree_retry as check
from amanat.rails.cashfree import CashfreePreAuthRail

from tests.test_cashfree_adapter_retry import FakeSandbox


def factory_on(sandbox):
    class Rail(CashfreePreAuthRail):
        def _call(self, method, path, *, version=None, **kw):
            return sandbox(method, path, version=version, **kw)

    Lossy = check.lossy(Rail)
    return lambda: Lossy(client_id="id", client_secret="secret")


class TestAgainstARailThatBehavesAsMeasured:
    def test_every_scenario_passes_and_each_says_what_it_counted(self):
        results = check.run(factory_on(FakeSandbox()))
        assert [r.name for r in results] == [
            "a reservation whose answer is lost", "a capture whose answer is lost",
            "a release whose answer is lost", "a whole session recovering from a lost capture",
            "a restarted session recovering from a lost capture"]
        assert all(r.ok for r in results), [(r.name, r.detail) for r in results if not r.ok]
        assert "captures on the rail=1" in results[1].detail and "voids on the rail=1" in results[2].detail

    def test_main_exits_zero_and_prints_a_line_per_scenario(self):
        lines = []
        code = check.main(factory=factory_on(FakeSandbox()), out=lines.append)
        text = "\n".join(lines)
        assert code == 0 and text.count("PASS") == 5 and "FAIL" not in text and "every retry acted once" in text

    def test_the_answer_really_is_lost_in_every_scenario(self):
        """A check whose fault never fires proves nothing: each detail says lost=True."""
        for r in check.run(factory_on(FakeSandbox())):
            if "session" not in r.name:
                assert "lost=True" in r.detail, r


class TestAgainstARailThatDoesNotHonourAKey:
    class Forgetful(FakeSandbox):
        """Like the sandbox, except that it forgets every idempotency key."""

        def _authorise(self, oid, body, headers):
            return super()._authorise(oid, body, {})

    def test_the_capture_scenario_fails_with_the_rails_own_refusal(self):
        results = {r.name: r for r in check.run(factory_on(self.Forgetful()))}
        cap = results["a capture whose answer is lost"]
        assert cap.ok is False and "RailError" in cap.detail and "Duplicate capture_id" in cap.detail

    def test_main_exits_one_and_says_not_to_rely_on_it(self):
        lines = []
        code = check.main(factory=factory_on(self.Forgetful()), out=lines.append)
        text = "\n".join(lines)
        assert code == 1 and "FAIL" in text and "do not rely on idempotency" in text

    def test_a_scenario_that_raises_does_not_stop_the_others(self):
        results = check.run(factory_on(self.Forgetful()))
        assert len(results) == 5 and any(r.ok for r in results), "the reservation scenario does not need a key on captures"
