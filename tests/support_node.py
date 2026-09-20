"""Run the verifier page's real JavaScript under Node.

`amanat.evidence.render._VERIFY_JS` is the code a browser runs to re-verify a
packet. Testing a Python port of it proves only that the port agrees with
Python; running the shipped source proves the page agrees. The functions the
tests reach for (`canonical`, `canonicalV1`, `digestInputFor`) are pure and
touch no DOM.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from amanat.evidence.render import _VERIFY_JS

_RUNNER = r"""
const chunks = [];
process.stdin.on('data', c => chunks.push(c));
process.stdin.on('end', async () => {
  const { src, expr, inputs } = JSON.parse(chunks.join(''));
  const page = new Function(
    src + '\n;return { canonical, canonicalV1, digestInputFor, assessTrust };')();
  const sha256 = s => require('crypto').createHash('sha256').update(s, 'utf8').digest('hex');
  const fn = new Function('page', 'sha256', 'return (' + expr + ');')(page, sha256);
  const out = [];
  for (const i of inputs) {
    try { out.push({ ok: await fn(i) }); } catch (e) { out.push({ error: String(e.message || e) }); }
  }
  process.stdout.write(JSON.stringify(out));
});
"""


def require_node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI: the browser verifier is tested by "
                        "executing its real source")
        pytest.skip("node not installed; browser-verifier parity tests need it")
    return node


def run_page_js(expr: str, inputs: list) -> list[dict]:
    """Apply the JS function expression `expr` to each input, in the real page code.

    `expr` sees `page` (the page's functions) and `sha256` (hex digest of a
    string). Each result is `{"ok": value}` or `{"error": message}`.
    """
    node = require_node()
    proc = subprocess.run(
        [node, "-e", _RUNNER], capture_output=True, text=True, timeout=60,
        input=json.dumps({"src": _VERIFY_JS, "expr": expr, "inputs": inputs},
                         ensure_ascii=True))
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr[-800:]}")
    return json.loads(proc.stdout)
