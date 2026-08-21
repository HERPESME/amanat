"""Produce an authorized-but-uncaptured payment, so capture can be measured.

    uv run --with httpx --with cryptography python -m amanat.rails.authorize

Payment links auto-capture, and S2S payment creation is not enabled on a
self-serve test account — so neither route can produce a payment sitting in
`authorized`. Razorpay Checkout against an order created with
`payment_capture: 0` can, but it needs a browser.

This serves that browser page on localhost, waits for the callback, and prints
the payment id. Test mode throughout: no bank or card network is contacted and
no real money moves.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from amanat import env
from amanat.rails.base import RailError
from amanat.rails.razorpay import RazorpayTestRail

PORT = 8799
_result: dict = {}
_done = threading.Event()

PAGE = """<!doctype html>
<meta charset="utf-8"><title>Amanat — authorize probe</title>
<style>
  body {{ font: 15px/1.6 system-ui, sans-serif; max-width: 34rem;
         margin: 4rem auto; padding: 0 1.5rem; color: #1a1a1a; }}
  code {{ background: #f4f4f5; padding: .1rem .35rem; border-radius: 3px; }}
  .note {{ color: #666; font-size: 13px; }}
  button {{ font: inherit; padding: .7rem 1.4rem; border: 0; border-radius: 6px;
            background: #1a1a1a; color: #fff; cursor: pointer; }}
  #out {{ margin-top: 1.5rem; padding: 1rem; border-radius: 6px; display: none; }}
  .ok {{ background: #e8f5e9; }} .bad {{ background: #ffebee; }}
</style>
<h2>Authorize-only payment</h2>
<p>Order <code>{order_id}</code> was created with <code>payment_capture: 0</code>,
so paying it leaves the payment <strong>authorized but not captured</strong> —
which is the state needed to test whether a partial capture is accepted.</p>
<p class="note">Test mode. No bank or card network is contacted and no real money
moves. Pay with UPI <code>success@razorpay</code>, or any test card with a 4–10
digit OTP.</p>
<button onclick="pay()">Pay ₹{rupees}</button>
<div id="out"></div>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
function show(cls, html) {{
  const o = document.getElementById('out');
  o.className = cls; o.innerHTML = html; o.style.display = 'block';
}}
function pay() {{
  new Razorpay({{
    key: "{key_id}",
    order_id: "{order_id}",
    amount: {amount},
    currency: "INR",
    name: "Amanat",
    description: "authorize-only probe",
    prefill: {{ contact: "9876543210", email: "probe@example.com" }},
    handler: function (r) {{
      show('ok', "Authorized: <code>" + r.razorpay_payment_id +
                 "</code><br><span class=note>You can close this tab.</span>");
      fetch('/done', {{ method: 'POST',
        body: JSON.stringify({{ payment_id: r.razorpay_payment_id }}) }});
    }},
    modal: {{ ondismiss: function () {{ show('bad', 'Cancelled.'); }} }}
  }}).open();
}}
</script>
"""


class _Handler(BaseHTTPRequestHandler):
    page = ""

    def do_GET(self):                                    # noqa: N802
        body = self.page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                   # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        try:
            _result.update(json.loads(self.rfile.read(n) or b"{}"))
        except ValueError:
            pass
        self.send_response(204)
        self.end_headers()
        _done.set()

    def log_message(self, *a):                           # silence the access log
        pass


def main() -> int:
    env.load()
    try:
        rail = RazorpayTestRail()
    except RailError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    amount = 620_00
    order = rail.create_order(amount, manual_capture=True,
                              notes={"purpose": "authorize-only probe"})
    print(f"\n  order {order['id']} created with payment_capture=0")

    _Handler.page = PAGE.format(order_id=order["id"], amount=amount,
                                rupees=f"{amount / 100:,.2f}",
                                key_id=os.environ["RAZORPAY_KEY_ID"])
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{PORT}/"
    print(f"  opening {url}")
    print("  pay with UPI success@razorpay, or a test card with a 4-10 digit OTP\n")
    try:
        webbrowser.open(url)
    except Exception:                                    # noqa: BLE001
        pass

    if not _done.wait(timeout=600):
        print("  timed out waiting for payment", file=sys.stderr)
        return 1
    server.shutdown()

    pid = _result.get("payment_id", "")
    payment = rail.fetch_payment(pid)
    print(f"  payment {pid}")
    print(f"    status   {payment['status']}")
    print(f"    amount   {payment['amount']}")
    print(f"    captured {payment.get('captured')}\n")

    if payment["status"] == "authorized":
        print("  Now measure partial capture:")
        print(f"    python -m amanat.rails.probe --capture {pid} 47000\n")
    else:
        print(f"  Expected 'authorized', got {payment['status']!r} — "
              "manual capture did not hold on this route.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
