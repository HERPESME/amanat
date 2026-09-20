"""A harness for Cashfree's UPI pre-authorization sandbox.

It drives the real adapter (`amanat.rails.cashfree`) and watches its one transport point, so a
probe records exactly what was sent and what came back. Only the sandbox is reachable: the
adapter refuses any other host, and the authorisation is forced with `POST /simulate`, so what a
probe measures is Cashfree's sandbox API, not an issuer.

Requests are recorded as method, path and JSON body. Headers are never recorded, so a
credential cannot reach the store through them; bodies are redacted by the runner.
"""
from __future__ import annotations

import secrets as _secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

import httpx

from amanat.probes.model import Exchange, OpResult, status_class
from amanat.rails.cashfree import ORDERS_VERSION, SANDBOX_BASE, CashfreePreAuthRail
from amanat.rails.semantics import Environment

MIN_SECRET = 6
_CUSTOMER = {"customer_id": "amanat_probe_cust", "customer_email": "probe@example.com",
             "customer_phone": "9999999999"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TracingCashfree(CashfreePreAuthRail):
    """The real adapter with its transport observed. Every call becomes a `trace` entry."""

    def __init__(self, *args, clock: Callable[[], str] = _now, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.trace: list[dict] = []
        self._extra_headers: dict[str, str] = {}
        self._clock = clock

    def _headers(self, version: str) -> dict:
        return {**super()._headers(version), **self._extra_headers}

    def _send(self, method: str, path: str, *, version: str, **kw) -> tuple[int, dict]:
        return CashfreePreAuthRail._call(self, method, path, version=version, **kw)

    def _call(self, method: str, path: str, *, version: str = ORDERS_VERSION, **kw) -> tuple[int, dict]:
        entry = {"method": method, "path": path, "json": kw.get("json"),
                 "status": None, "response": None, "error": None, "at": self._clock()}
        self.trace.append(entry)
        try:
            status, body = self._send(method, path, version=version, **kw)
        except httpx.HTTPError as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        entry["status"], entry["response"] = status, body
        return status, body


class CashfreeHarness:
    """`hold`, `capture`, `void`, `fetch` and `capture_parallel` against the sandbox, and the repeats
    (`recreate_order`, `pay_again`) that ask whether a retry acts once."""

    rail_id = "cashfree_preauth"
    environment = Environment.SANDBOX
    name = "cashfree_preauth/sandbox"
    ops = frozenset({"hold", "capture", "void", "fetch", "capture_parallel", "recreate_order", "pay_again"})

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, *,
                 base: str = SANDBOX_BASE, rail_factory: Callable[..., TracingCashfree] = TracingCashfree,
                 settle_seconds: float = 2.0, clock: Callable[[], str] = _now) -> None:
        self._factory, self._clock, self.settle_seconds = rail_factory, clock, settle_seconds
        self.rail = rail_factory(client_id, client_secret, base=base, clock=clock)
        self._base = base

    @property
    def secrets(self) -> list[str]:
        return [s for s in (self.rail.client_id, self.rail.client_secret) if len(s) >= MIN_SECRET]

    def run_op(self, op: str, args: dict) -> OpResult:
        if op not in self.ops:
            raise ValueError(f"unknown operation {op!r}; this harness offers {sorted(self.ops)}")
        return getattr(self, f"_op_{op}")(**args)

    # ------------------------------------------------------------------- recording

    def _new_rail(self) -> TracingCashfree:
        return self._factory(self.rail.client_id, self.rail.client_secret, base=self._base,
                             clock=self._clock)

    @staticmethod
    def _exchange(rail: TracingCashfree, label: str, call: Callable, *, extra: dict | None = None) -> Exchange:
        """Make one call and return it as an exchange. A transport failure is kept, not raised."""
        before = len(rail.trace)
        try:
            call()
        except httpx.HTTPError:
            pass                                   # the failure is in the trace entry
        e = rail.trace[before]
        request = {"method": e["method"], "path": e["path"]}
        if e["json"] is not None:
            request["json"] = e["json"]
        if extra:
            request.update(extra)
        return Exchange(label, request, e["status"], e["response"], e["error"], e["at"])

    def idempotency_key(self, hold: dict, name: str) -> str:
        """A stable UUID for (this hold, this key name): the same name repeats, another hold differs."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{hold['order_id']}:{name}"))

    # ---------------------------------------------------------------- operations

    def _op_hold(self, amount: int) -> OpResult:
        r = self.rail
        order_id = f"amanat_pr_{int(time.time())}_{_secrets.token_hex(3)}"
        exs: list[Exchange] = []

        def step(label, call):
            e = self._exchange(r, label, call)
            exs.append(e)
            return e

        made = step("order_create", lambda: r.create_preauth_order(order_id, amount, customer=_CUSTOMER))
        session = (made.response or {}).get("payment_session_id") if isinstance(made.response, dict) else None
        if made.status != 200 or not session:
            return OpResult(exs, ok=False)
        paid = step("pay_collect", lambda: r.pay_upi_collect(session))
        cf_id = (paid.response or {}).get("cf_payment_id") if isinstance(paid.response, dict) else None
        if paid.status != 200 or not cf_id:
            return OpResult(exs, ok=False)
        if step("simulate", lambda: r.simulate_success(cf_id)).status != 200:
            return OpResult(exs, ok=False)
        time.sleep(self.settle_seconds)
        step("order", lambda: r.fetch_order(order_id))
        step("payments", lambda: r.fetch_payments(order_id))
        # The session id is what a repeated payment must present. It is a handle, never a record: the
        # runner redacts it from every exchange, and the handle itself is not stored.
        return OpResult(exs, handle={"order_id": order_id, "cf_payment_id": cf_id, "amount": amount,
                                     "payment_session_id": session})

    def _op_capture(self, hold: dict, amount: int, idempotency_key: str | None = None) -> OpResult:
        r = self.rail
        extra = None
        if idempotency_key:
            key = self.idempotency_key(hold, idempotency_key)
            r._extra_headers = {"x-idempotency-key": key}
            extra = {"idempotency_key": key}
        try:
            e = self._exchange(r, "capture", lambda: r.capture(hold["order_id"], amount), extra=extra)
        finally:
            r._extra_headers = {}
        return OpResult([e])

    def _op_void(self, hold: dict, idempotency_key: str | None = None) -> OpResult:
        r = self.rail
        extra = None
        if idempotency_key:
            key = self.idempotency_key(hold, idempotency_key)
            r._extra_headers = {"x-idempotency-key": key}
            extra = {"idempotency_key": key}
        try:
            e = self._exchange(r, "void", lambda: r.void(hold["order_id"]), extra=extra)
        finally:
            r._extra_headers = {}
        return OpResult([e])

    def _op_recreate_order(self, hold: dict, amount: int) -> OpResult:
        """Ask for an order under an id that already exists: is the repeat refused, or is it a second order?"""
        r = self.rail
        return OpResult([self._exchange(r, "order_recreate", lambda: r.create_preauth_order(
            hold["order_id"], amount, customer=_CUSTOMER))])

    def _op_pay_again(self, hold: dict) -> OpResult:
        """Submit the payment again against an order that is already authorised."""
        r = self.rail
        return OpResult([self._exchange(r, "pay_again", lambda: r.pay_upi_collect(hold["payment_session_id"]))])

    def _op_fetch(self, hold: dict) -> OpResult:
        r, oid = self.rail, hold["order_id"]
        return OpResult([self._exchange(r, "order", lambda: r.fetch_order(oid)),
                         self._exchange(r, "payments", lambda: r.fetch_payments(oid)),
                         self._exchange(r, "refunds", lambda: r.fetch_refunds(oid))])

    def _op_capture_parallel(self, hold: dict, amounts: list[int]) -> OpResult:
        """Fire the captures together. The summary is derived from the recorded exchanges."""
        n = len(amounts)
        rails = [self._new_rail() for _ in amounts]
        barrier = threading.Barrier(n)
        out: list[Exchange | None] = [None] * n

        def work(i: int) -> None:
            barrier.wait()
            out[i] = self._exchange(rails[i], f"capture_{i + 1}",
                                    lambda: rails[i].capture(hold["order_id"], amounts[i]))

        threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        exs = [e for e in out if e is not None]
        klass = [status_class(e.status) for e in exs]
        won, refused = klass.count("2xx"), klass.count("4xx")
        unanswered = n - won - refused
        summary = Exchange("summary", {"derived": True, "from": [e.label for e in exs]}, 200, {
            "succeeded": won, "refused": refused, "unanswered": unanswered,
            "single_winner": won == 1 and unanswered == 0, "multiple_winners": won >= 2,
        }, None, self._clock())
        return OpResult([*exs, summary])
