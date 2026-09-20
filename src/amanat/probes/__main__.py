"""Run the rail probes.

    python -m amanat.probes list
    python -m amanat.probes run [PROBE_ID ...] [--rail cashfree_preauth] [--dry-run]

A run places real holds in the vendor's *sandbox* using the credentials in `.env`, records every
exchange (redacted) as one line in the evidence store, and prints what each probe found. It
moves no real money: the Cashfree harness refuses any host but the sandbox. A run in which no
probe learned anything (wrong credentials, a sandbox that is down) exits 1, so a scheduled job
goes red, and the outage is still recorded.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from amanat import env
from amanat.probes import catalogue, runner
from amanat.rails.base import RailError
from amanat.registry import store

DEFAULT_STORE_DIR = store.STORE_DIR


def _harness_for(rail_id: str):
    if rail_id == "cashfree_preauth":
        from amanat.probes.cashfree import CashfreeHarness
        return CashfreeHarness()
    raise RailError(f"no harness for rail {rail_id!r}")


def _verdict(f: dict) -> str:
    return {True: "supported", False: "not supported", None: "inconclusive"}[f["supported"]]


def main(argv: list[str] | None = None, *, harness=None, store_dir: Path | None = None,
         out=print) -> int:
    ap = argparse.ArgumentParser(prog="python -m amanat.probes", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the probes and the question each asks")
    r = sub.add_parser("run", help="run probes against the sandbox and record them")
    r.add_argument("probe_ids", nargs="*")
    r.add_argument("--rail", default="cashfree_preauth")
    r.add_argument("--dry-run", action="store_true", help="print findings, record nothing")
    r.add_argument("-v", "--verbose", action="store_true", help="also print every exchange")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for p in catalogue.PROBES.values():
            out(f"{p.probe_id}\n    {p.summary}")
        return 0

    chosen = args.probe_ids or [p.probe_id for p in catalogue.for_rail(args.rail)]
    unknown = [pid for pid in chosen if pid not in catalogue.PROBES]
    if unknown:
        out(f"unknown probe(s): {', '.join(unknown)} (see `list`)")
        return 2
    if harness is None:
        env.load()
        try:
            harness = _harness_for(catalogue.PROBES[chosen[0]].rail_id)
        except RailError as exc:
            out(f"\n  ✗ {exc}\n  Put the sandbox credentials in .env")
            return 2

    learned_something = False
    for pid in chosen:
        probe = catalogue.PROBES[pid]
        obs = runner.run(probe, harness)
        out(f"\n{pid}")
        if args.verbose:
            for e in obs["exchanges"]:
                out(f"    {e['step']}/{e['label']:14s} HTTP {e['status']}  {runner._message(e['response']) or e['error'] or ''}"[:200])
        for f in obs["findings"]:
            out(f"  {f['capability']:38s} {_verdict(f):14s} {f['basis']}")
            learned_something |= f["supported"] is not None
        if not args.dry_run:
            path = (store_dir / f"{runner.stream_for(probe.rail_id)}.jsonl") if store_dir else None
            h = runner.record(obs, path)
            out(f"  recorded {h[:12]}")
    if not learned_something:
        out("\n  ✗ no probe learned anything: the sandbox could not be asked (credentials? outage?)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
