"""Check which credentials are present and whether they actually work.

    uv run --with google-genai --with httpx --with cryptography python -m amanat.doctor

Every check is read-only and moves no money. Each one reports what it found,
what it means, and — when something is missing — the exact next step, because
"not configured" and "configured but rejected" need very different responses and
a bare red cross tells you neither.

Nothing here is required. The governed core, the containment suite and the demo
all run with zero credentials; that is a design property, not an oversight.
"""
from __future__ import annotations

import os
import sys

from amanat import env

OK, MISSING, BAD = "\033[32m  ok  \033[0m", "\033[33m  --  \033[0m", "\033[31m fail \033[0m"


def _line(status: str, name: str, detail: str) -> None:
    print(f"  [{status}] {name:<22s} {detail}")


def check_gemini() -> bool:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        _line(MISSING, "GEMINI_API_KEY", "not set — the agent CLI needs this or ANTHROPIC_API_KEY")
        return False
    try:
        from google import genai

        from amanat.orchestrator.backends import GeminiBackend

        # models.get, not models.list: it checks the key AND that the exact
        # model this project calls is reachable. list() returns a lazy pager
        # that reports a closed-client error if you never consume it, which
        # reads as a rejected key — a false negative in a diagnostic is worse
        # than no diagnostic.
        model = GeminiBackend().model
        # Bind the client: an inline genai.Client(...).models.get(...) can be
        # garbage-collected mid-call, which surfaces as "client has been closed"
        # and looks exactly like a rejected key.
        client = genai.Client(api_key=key)
        client.models.get(model=model)
        _line(OK, "GEMINI_API_KEY", f"accepted · {model} reachable")
        return True
    except ImportError:
        _line(MISSING, "GEMINI_API_KEY", "set, but google-genai is not installed "
                                         "(add --with google-genai)")
        return False
    except Exception as exc:                                   # noqa: BLE001
        _line(BAD, "GEMINI_API_KEY", f"rejected: {type(exc).__name__}: {str(exc)[:90]}")
        return False


def check_anthropic() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        _line(MISSING, "ANTHROPIC_API_KEY", "not set — optional, Gemini covers the agent")
        return False
    try:
        import anthropic

        from amanat.orchestrator.backends import AnthropicBackend

        model = AnthropicBackend().model
        client = anthropic.Anthropic(api_key=key)
        client.models.retrieve(model)
        _line(OK, "ANTHROPIC_API_KEY", f"accepted · {model} reachable")
        return True
    except ImportError:
        _line(MISSING, "ANTHROPIC_API_KEY", "set, but the anthropic package is not installed")
        return False
    except Exception as exc:                                   # noqa: BLE001
        _line(BAD, "ANTHROPIC_API_KEY", f"rejected: {type(exc).__name__}: {str(exc)[:90]}")
        return False


def check_razorpay() -> bool:
    """Fetch the orders list. Read-only, and it proves auth without side effects."""
    key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    if not (key_id and secret):
        _line(MISSING, "RAZORPAY", "not set — only needed to demo the partial-capture negative")
        return False
    if not key_id.startswith("rzp_test_"):
        _line(BAD, "RAZORPAY", f"key_id starts {key_id[:9]!r} — expected 'rzp_test_'. "
                               "Never put a live key in this project")
        return False
    try:
        import httpx
        r = httpx.get("https://api.razorpay.com/v1/orders?count=1",
                      auth=(key_id, secret), timeout=20)
    except Exception as exc:                                   # noqa: BLE001
        _line(BAD, "RAZORPAY", f"network error: {exc}")
        return False
    if r.status_code == 200:
        _line(OK, "RAZORPAY", "test-mode credentials accepted")
        return True
    if r.status_code == 401:
        _line(BAD, "RAZORPAY", "401 — key_id/secret pair rejected")
        return False
    _line(BAD, "RAZORPAY", f"HTTP {r.status_code}: {r.text[:80]}")
    return False


def check_setu() -> bool:
    """Setu UAT is self-serve; presence of both halves is what matters here."""
    cid = os.environ.get("SETU_CLIENT_ID", "").strip()
    secret = os.environ.get("SETU_CLIENT_SECRET", "").strip()
    if not (cid or secret):
        _line(MISSING, "SETU", "not set — self-serve at bridge.setu.co, host uatapi.setu.co")
        return False
    if not (cid and secret):
        _line(BAD, "SETU", "only half configured — both CLIENT_ID and CLIENT_SECRET are needed")
        return False
    _line(OK, "SETU", "both halves present (not exercised — UMAP needs an onboarded entity)")
    return True


def check_cashfree() -> bool:
    app_id = os.environ.get("CASHFREE_APP_ID", "").strip()
    secret = os.environ.get("CASHFREE_SECRET_KEY", "").strip()
    if not (app_id or secret):
        _line(MISSING, "CASHFREE", "not set — sandbox signup is self-serve, "
                                   "pre-auth needs a support request")
        return False
    if not (app_id and secret):
        _line(BAD, "CASHFREE", "only half configured — both APP_ID and SECRET_KEY are needed")
        return False
    _line(OK, "CASHFREE", "both halves present (pre-auth enablement is separate from these)")
    return True


def main() -> int:
    loaded = env.load()
    print("\n\033[1mAMANAT — credential check\033[0m")
    print(f"  \033[2m.env supplied: {', '.join(loaded) if loaded else 'nothing'}\033[0m\n")

    print("  \033[1mLLM — one of these is needed for the agent CLI\033[0m")
    llm = check_gemini() | check_anthropic()

    print("\n  \033[1mPayment rails — all optional\033[0m")
    check_razorpay()
    check_setu()
    check_cashfree()

    print("\n  \033[1mWhat runs right now\033[0m")
    _line(OK, "containment suite", "pytest tests/ — 131 tests, no credentials needed")
    _line(OK, "demo walkthrough", "python -m amanat.demo — no credentials needed")
    _line(OK if llm else MISSING, "agent CLI",
          "python -m amanat.orchestrator.cli"
          + ("" if llm else "  ← blocked, set an LLM key"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
