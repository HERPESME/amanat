"""Run a probe against a harness, record it, and read the record back.

One run is one line in the evidence store (`probes.<rail_id>`): the question (`definition`, and
its hash), every exchange (redacted; headers are never recorded), the steps that were skipped
and why, and a finding per capability the probe speaks to. A capability with no matching rule,
or whose deciding step gave no answer, gets `supported: null` and the reason — never a refusal.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from amanat.probes.model import ANSWERS, Exchange, OpResult, Probe, Ref, Rule, status_class
from amanat.registry import store

MIN_SECRET = 6
SECRET_KEYS = frozenset({
    "payment_session_id", "customer_details", "x-client-secret", "x-client-id", "client_secret",
    "client_id", "api_key", "secret", "password", "access_token", "refresh_token", "session_token",
})
_MISSING = object()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stream_for(rail_id: str) -> str:
    return f"probes.{rail_id}"


# ------------------------------------------------------------------------------- redaction

def redact(value: Any, secrets: tuple[str, ...] | list[str] = ()) -> Any:
    """A copy without credential-like keys, and with known secret values scrubbed from strings."""
    for s in secrets:
        if len(s) < MIN_SECRET:
            raise ValueError("a secret shorter than %d characters cannot be scrubbed safely" % MIN_SECRET)

    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()
                    if not (isinstance(k, str) and k.lower() in SECRET_KEYS)}
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, str):
            for s in secrets:
                v = v.replace(s, "[redacted]")
        return v

    return clean(value)


# --------------------------------------------------------------------------------- reading

def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return _MISSING
    return obj


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return a == b


def _message(response: Any) -> str:
    if isinstance(response, dict):
        for key in ("message", "error_description", "detail"):
            if isinstance(response.get(key), str):
                return response[key]
        err = response.get("error")
        if isinstance(err, dict) and isinstance(err.get("description") or err.get("message"), str):
            return err.get("description") or err.get("message")
        if isinstance(err, str):
            return err
    return json.dumps(response, ensure_ascii=False)[:120] if response else ""


class _Fill(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _select(rule: Rule, exchanges: list[Exchange]) -> Exchange | None:
    if not exchanges:
        return None
    if rule.label:
        return next((e for e in exchanges if e.label == rule.label), None)
    return exchanges[-1]


def _precondition(rule: Rule, by_step: dict[str, list[Exchange]]) -> str | None:
    """Why this rule does not apply yet, or None if every step it needs answered as required."""
    for step, want in rule.after:
        exs = by_step.get(step, [])
        got = status_class(exs[-1].status) if exs else "not run"
        if got != want:
            return f"step '{step}' did not answer {want} ({got})"
    return None


def _matches(rule: Rule, ex: Exchange) -> bool:
    klass = status_class(ex.status)
    if klass not in ANSWERS or (rule.status and klass != rule.status):
        return False
    return all(_same(_dig(ex.response, path), want) for path, want in rule.where)


def _why_not(rules: list[Rule], by_step: dict[str, list[Exchange]], state: dict[str, str],
             why_skipped: dict[str, str]) -> str:
    reasons = []
    for rule in rules:
        blocked = _precondition(rule, by_step)
        if blocked:
            reasons.append(blocked)
    for step in dict.fromkeys(r.step for r in rules):
        if state.get(step) == "skipped":
            reasons.append(f"step '{step}' was skipped: {why_skipped[step]}")
            continue
        exs = by_step.get(step, [])
        last = exs[-1] if exs else None
        if last is None:
            reasons.append(f"step '{step}' recorded no exchange")
        elif last.error:
            reasons.append(f"step '{step}' failed: {last.error}")
        elif status_class(last.status) not in ANSWERS:
            reasons.append(f"step '{step}' returned HTTP {last.status}, which is not an answer")
        else:
            reasons.append(f"step '{step}' returned HTTP {last.status}, which no rule reads")
    return "inconclusive — " + "; ".join(dict.fromkeys(reasons))


def _findings(probe: Probe, by_step: dict[str, list[Exchange]], state: dict[str, str],
              why_skipped: dict[str, str]) -> list[dict]:
    out = []
    for cap in dict.fromkeys(r.capability for r in probe.rules):
        rules = [r for r in probe.rules if r.capability == cap]
        hit = None
        for rule in rules:
            if _precondition(rule, by_step):
                continue
            ex = _select(rule, by_step.get(rule.step, []))
            if ex is not None and _matches(rule, ex):
                hit = (rule, ex)
                break
        if hit:
            rule, ex = hit
            basis = rule.basis.format_map(_Fill(status=ex.status, message=_message(ex.response)))
            out.append({"capability": cap, "supported": rule.supported, "basis": basis, "step": rule.step})
        else:
            out.append({"capability": cap, "supported": None, "basis": _why_not(rules, by_step, state, why_skipped),
                        "step": rules[0].step})
    return out


# --------------------------------------------------------------------------------- running

def run(probe: Probe, harness, *, clock: Callable[[], str] = _now) -> dict:
    """Perform the probe's steps against `harness` and return the observation (plain JSON)."""
    secrets = tuple(getattr(harness, "secrets", ()))
    started = clock()
    handles: dict[str, Any] = {}
    state: dict[str, str] = {}
    by_step: dict[str, list[Exchange]] = {}
    recorded: list[dict] = []
    skipped: list[dict] = []
    for step in probe.steps:
        needs = [v.step for v in step.args.values() if isinstance(v, Ref)]
        blocker = next((n for n in needs if state.get(n) != "ok"), None)
        if blocker:
            state[step.name] = "skipped"
            skipped.append({"step": step.name, "why": f"depends on '{blocker}', which did not complete"})
            continue
        args = {k: handles[v.step] if isinstance(v, Ref) else v for k, v in step.args.items()}
        try:
            result = harness.run_op(step.op, args)
        except Exception as exc:                        # noqa: BLE001 - recorded, not raised
            result = OpResult([Exchange(step.op, {}, None, None, error=f"{type(exc).__name__}: {exc}")],
                              ok=False)
        handles[step.name] = result.handle
        state[step.name] = "ok" if result.ok else "failed"
        # Redact before anything reads the exchange: the findings quote its message and error,
        # so a secret that survived to this point would be copied into the record.
        clean = [Exchange(e.label, redact(e.request, secrets), e.status, redact(e.response, secrets),
                          redact(e.error, secrets) if e.error else None, e.at or clock())
                 for e in result.exchanges]
        by_step[step.name] = clean
        for e in clean:
            recorded.append({"step": step.name, "op": step.op, "label": e.label, "request": e.request,
                             "status": e.status, "response": e.response, "error": e.error, "at": e.at})
    definition = probe.definition()
    return {
        "probe_id": probe.probe_id, "rail_id": probe.rail_id,
        "environment": probe.environment.value, "harness": harness.name,
        "started_at": started, "finished_at": clock(),
        "definition": definition,
        "definition_sha256": hashlib.sha256(json.dumps(
            definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest(),
        "exchanges": recorded, "skipped": skipped,
        "findings": _findings(probe, by_step, state, {k["step"]: k["why"] for k in skipped}),
    }


# --------------------------------------------------------------------------- the store

def record(observation: dict, path: Path | None = None, *, at: str | None = None) -> str:
    """Append the run to the evidence store and return its evidence hash."""
    path = path or store.stream_path(stream_for(observation["rail_id"]))
    return store.append(path, "probe_run", observation, at=at or observation["finished_at"])


def history(path: Path) -> dict[tuple[str, str], list[dict]]:
    """Every conclusive finding of every (probe, capability), oldest first."""
    out: dict[tuple[str, str], list[dict]] = {}
    for h, rec in store.records(path):
        if rec["kind"] != "probe_run":
            continue
        data = rec["data"]
        for f in data["findings"]:
            if f["supported"] is None:
                continue
            out.setdefault((data["probe_id"], f["capability"]), []).append({
                "supported": f["supported"], "basis": f["basis"], "observed_at": rec["at"],
                "evidence_hash": h, "environment": data["environment"],
            })
    return out


def latest_findings(path: Path) -> dict[tuple[str, str], dict]:
    return {k: v[-1] for k, v in history(path).items()}


def changes(hist: list[dict]) -> list[dict]:
    return [{"on": b["observed_at"][:10], "from": a["supported"], "to": b["supported"]}
            for a, b in zip(hist, hist[1:]) if a["supported"] != b["supported"]]
