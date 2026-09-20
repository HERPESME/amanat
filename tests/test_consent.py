"""The human's grant is signed where the human's key lives.

A session that is *handed* the human's private key can sign consent that no human
gave; the key then lives in the orchestrator, which is the party the grant is
meant to constrain. So the session accepts a signature made elsewhere, verifies
it, and never sees a key. The initial grant gets the same treatment: an AP2
mandate the human signed, or an entry that says plainly it was not.
"""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from amanat.dispute.adjudicate import Finding, adjudicate
from amanat.evidence.chain import Actor, EventType
from amanat.interop.ap2 import sign_mandate, to_open_payment_mandate
from amanat.orchestrator.session import AgentSession
from amanat.policy.consent import build_widening, sign_consent, verify_consent
from amanat.policy.envelope import Envelope
from amanat.rails.simulator import SimulatedRail


def _env(subject="cap"):
    return Envelope(subject=subject, max_total=100_000, max_per_txn=100_000,
                    allowed_payees=["citycabs"],
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1))


def _session(env=None, **kw):
    return AgentSession(env or _env(), SimulatedRail("sbmd", customer_balance=1_000_000), **kw)


def _hex(key):
    return key.public_key().public_bytes_raw().hex()


def _consent(session, key, *, to=130_000, reason="rider approved"):
    body = build_widening(session.envelope, new_max_total=to, new_max_per_txn=to,
                          reason=reason, public_key_hex=_hex(key))
    return sign_consent(body, key)


def _widenings(session):
    return [e for e in session.chain.entries
            if e.event_type is EventType.ENVELOPE and e.payload.get("event") == "envelope_widened"]


class TestConsentSignedElsewhere:
    def test_a_consent_signed_by_the_human_widens_the_envelope(self):
        s, key = _session(), Ed25519PrivateKey.generate()
        consent = _consent(s, key)
        r = s.approve_raise_signed(consent)
        assert r.ok and s.envelope.max_total == 130_000 and s.envelope.max_per_txn == 130_000
        (entry,) = _widenings(s)
        assert entry.actor is Actor.HUMAN
        assert entry.payload["signature"] == consent["signature"]

    def test_any_edit_to_a_signed_consent_breaks_it(self):
        s, key = _session(), Ed25519PrivateKey.generate()
        c = _consent(s, key)
        assert verify_consent(c) is True
        assert verify_consent({**c, "to": {"max_total": 999_999, "max_per_txn": 999_999}}) is False
        assert verify_consent({**c, "reason": "different"}) is False

    def test_a_tampered_consent_is_refused_and_recorded(self):
        s, key = _session(), Ed25519PrivateKey.generate()
        c = {**_consent(s, key), "to": {"max_total": 999_999, "max_per_txn": 999_999}}
        r = s.approve_raise_signed(c)
        assert r.ok is False and s.envelope.max_total == 100_000
        assert s.chain.refusals()[-1].payload["rule"] == "consent_rejected"

    def test_a_consent_cannot_be_replayed_once_applied(self):
        s, key = _session(), Ed25519PrivateKey.generate()
        c = _consent(s, key)
        assert s.approve_raise_signed(c).ok
        again = s.approve_raise_signed(c)                 # `from` no longer matches
        assert again.ok is False and len(_widenings(s)) == 1

    def test_a_consent_that_lowers_a_cap_is_refused(self):
        s, key = _session(), Ed25519PrivateKey.generate()
        r = s.approve_raise_signed(_consent(s, key, to=50_000))
        assert r.ok is False and s.envelope.max_total == 100_000

    def test_a_consent_for_another_subject_is_refused(self):
        s, other = _session(), _session(_env("someone-else"))
        key = Ed25519PrivateKey.generate()
        assert s.approve_raise_signed(_consent(other, key)).ok is False

    def test_a_consent_with_a_float_or_junk_is_refused_not_raised(self):
        s, key = _session(), Ed25519PrivateKey.generate()
        import json
        body = build_widening(s.envelope, new_max_total=1300.5, new_max_per_txn=1300.5,
                              reason="x", public_key_hex=_hex(key))
        # a hostile signer signs whatever bytes it likes; sign_consent itself would refuse a float
        hostile = {**body, "signature": key.sign(
            json.dumps(body, sort_keys=True).encode("utf-8")).hex()}
        for junk in (None, [], "text", hostile):
            assert s.approve_raise_signed(junk).ok is False
        assert s.envelope.max_total == 100_000

    def test_the_local_key_convenience_produces_an_ordinary_verifiable_consent(self):
        s = _session()
        assert s.approve_raise(130_000, Ed25519PrivateKey.generate(), reason="cli").ok
        assert verify_consent(_widenings(s)[0].payload) is True


class TestTheInitialGrant:
    def test_a_session_without_a_mandate_records_that_its_grant_is_unsigned(self):
        s = _session()
        assert s.chain.entries[0].payload["grant"] == {"signed": False}

    def test_a_signed_mandate_is_recorded_with_its_key_and_signature(self):
        env, key = _env(), Ed25519PrivateKey.generate()
        mandate = sign_mandate(to_open_payment_mandate(env), key)
        s = _session(env, mandate=mandate)
        assert s.chain.entries[0].payload["grant"] == {
            "signed": True, "kind": "ap2.open_payment_mandate",
            "public_key": _hex(key), "mandate_signature": mandate["signature"]}

    def test_a_mandate_that_does_not_verify_is_refused_at_construction(self):
        env, key = _env(), Ed25519PrivateKey.generate()
        mandate = sign_mandate(to_open_payment_mandate(env), key)
        mandate["constraints"][2]["max"] = 9_999_999           # edited after signing
        with pytest.raises(ValueError, match="not validly signed"):
            _session(env, mandate=mandate)

    def test_an_unsigned_mandate_is_refused_as_a_grant(self):
        with pytest.raises(ValueError, match="not validly signed"):
            _session(_env(), mandate=to_open_payment_mandate(_env()))

    def test_an_envelope_that_differs_from_its_mandate_is_refused(self):
        env, key = _env(), Ed25519PrivateKey.generate()
        mandate = sign_mandate(to_open_payment_mandate(env), key)
        bigger = Envelope(subject=env.subject, max_total=500_000, max_per_txn=500_000,
                          allowed_payees=["citycabs"], expires_at=env.expires_at)
        with pytest.raises(ValueError, match="does not match the mandate"):
            _session(bigger, mandate=mandate)

    def test_a_widening_must_come_from_the_key_that_signed_the_grant(self):
        env, human = _env(), Ed25519PrivateKey.generate()
        s = _session(env, mandate=sign_mandate(to_open_payment_mandate(env), human))
        stranger = Ed25519PrivateKey.generate()
        refused = s.approve_raise_signed(_consent(s, stranger))
        assert refused.ok is False and "grant" in refused.detail
        assert s.approve_raise_signed(_consent(s, human)).ok is True

    def test_the_adjudicator_refuses_a_mandate_that_is_not_the_grant_the_chain_ran_under(self):
        env, human = _env(), Ed25519PrivateKey.generate()
        s = _session(env, mandate=sign_mandate(to_open_payment_mandate(env), human))
        s.reserve(62_000, "citycabs")
        s.debit(47_000, "fare")
        impostor = sign_mandate(to_open_payment_mandate(env), Ed25519PrivateKey.generate())
        a = adjudicate(s.evidence_packet(), impostor, "unauthorized")
        assert a.finding is Finding.MANDATE_UNVERIFIED
        genuine = adjudicate(s.evidence_packet(),
                             sign_mandate(to_open_payment_mandate(env), human), "unauthorized")
        assert genuine.finding is not Finding.MANDATE_UNVERIFIED


class TestAConsentSignedInTheBrowserIsAcceptedByTheServer:
    """Node's real WebCrypto signs the bytes the page's own `canonical()` produces;
    Python must verify it. If the two sides ever canonicalise differently, every
    console re-approval would fail — or worse, verify something nobody signed."""

    def _browser_sign(self, body_for):
        from tests.support_node import run_page_js
        gen = run_page_js("""async () => {
            const { webcrypto } = require('crypto');
            const kp = await webcrypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
            const pub = Buffer.from(await webcrypto.subtle.exportKey('raw', kp.publicKey)).toString('hex');
            return { pub, jwk: await webcrypto.subtle.exportKey('jwk', kp.privateKey) };
        }""", [None])[0]["ok"]
        body = body_for(gen["pub"])
        sig = run_page_js("""async i => {
            const { webcrypto } = require('crypto');
            const key = await webcrypto.subtle.importKey('jwk', i.jwk, { name: 'Ed25519' }, false, ['sign']);
            const sig = await webcrypto.subtle.sign(
              { name: 'Ed25519' }, key, new TextEncoder().encode(page.canonical(i.body)));
            return Buffer.from(sig).toString('hex');
        }""", [{"jwk": gen["jwk"], "body": body}])[0]["ok"]
        return {**body, "signature": sig}, gen["pub"]

    def test_the_server_applies_a_consent_the_browser_signed(self):
        s = _session()
        consent, pub = self._browser_sign(lambda pub: build_widening(
            s.envelope, new_max_total=130_000, new_max_per_txn=130_000,
            reason="rider approved — ₹1,300 🚕", public_key_hex=pub))
        assert verify_consent(consent) is True
        assert s.approve_raise_signed(consent).ok is True
        assert _widenings(s)[0].payload["cnf"]["jwk"]["x"] == pub
