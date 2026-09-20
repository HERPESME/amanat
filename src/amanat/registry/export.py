"""Export the rail-semantics registry as versioned JSON, and publish its schema.

    python -m amanat.registry.export

`rails/semantics.py` is the truth the policy engine reads. This is the same table in a form
other tools can consume, generated the way `docs/RAIL_SEMANTICS.md` is: never edited by hand,
and a CI step fails if it has drifted from the table. The export is deterministic — it reads
no clock and no set — so a diff means the registry changed, not that it was regenerated.

`docs/registry/registry.schema.json` states the contract, including the project's rules
(cited or UNVERIFIED; an observation names its environment; permitted means supported AND
usable as fact). Validating against it is how a consumer inherits them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from amanat.rails.semantics import (
    CONCEPTS, HYPERSWITCH, RAILS, SOURCE_COPIES, Capability, Limit, RailProfile, SourceTier,
)
from amanat.probes import runner
from amanat.registry import store, watch

SCHEMA_VERSION = 2

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs" / "registry"
REGISTRY_PATH = OUT_DIR / "registry.json"
SCHEMA_PATH = OUT_DIR / "registry.schema.json"
_PACKAGED_SCHEMA = Path(__file__).with_name("registry.schema.json")


def schema() -> dict:
    return json.loads(_PACKAGED_SCHEMA.read_text(encoding="utf-8"))


def _verification(row: Capability | Limit, key: tuple[str, str, str], history: dict) -> dict | None:
    """When this row's quote was last re-checked against its source, and how that has gone.

    An UNVERIFIED row has no quote to re-read. The store keeps the checks it had while it was cited
    (that is what an append-only record is for), but the export must not say a quote was re-read
    for a row that no longer carries one.
    """
    if row.source_tier is SourceTier.UNVERIFIED:
        return None
    checks = [c for c in history.get(key, []) if c["result"] != watch.SKIPPED]
    if not checks:
        return None
    last = checks[-1]
    return {
        "result": last["result"],
        "detail": last["detail"],
        "checked_on": last["checked_at"][:10],
        "quote_current": last["quote_sha256"] == watch.quote_hash(row.quote),
        "evidence_hash": last["evidence_hash"],
        "checks": len(checks),
        "changes": [{"on": b["checked_at"][:10], "from": a["result"], "to": b["result"]}
                    for a, b in zip(checks, checks[1:]) if a["result"] != b["result"]],
    }


def _observation(rail_id: str, row: Capability | Limit, probes: dict) -> dict | None:
    """What the row's probe most recently found, and whether that agrees with the row."""
    if not row.probe_id:
        return None
    hist = probes.get(rail_id, {}).get((row.probe_id, row.name), [])
    if not hist:
        return None
    last = hist[-1]
    return {
        "probe_id": row.probe_id,
        "runs": len(hist),
        "latest": {"supported": last["supported"], "observed_on": last["observed_at"][:10],
                   "evidence_hash": last["evidence_hash"], "environment": last["environment"],
                   "agrees": last["supported"] is getattr(row, "supported", None)},
        "changes": runner.changes(hist),
    }


def _evidence(row: Capability | Limit, verification: dict | None, observation: dict | None) -> dict:
    return {
        "tier": row.source_tier.value,
        "usable_as_fact": row.is_fact,
        "environment": row.environment.value if row.environment else None,
        "obtained_on": row.obtained_on or None,
        "probe_id": row.probe_id or None,
        "citation": row.citation,
        "url": row.url,
        "quote": row.quote,
        "notes": row.notes,
        "verification": verification,
        "observation": observation,
    }


def _capability(rail: RailProfile, cap: Capability, history: dict, probes: dict) -> dict:
    v = _verification(cap, (rail.rail_id, "capability", cap.name), history)
    return {"name": cap.name, "supported": cap.supported, "permitted": rail.permits(cap.name),
            **_evidence(cap, v, _observation(rail.rail_id, cap, probes))}


def _limit(rail: RailProfile, lim: Limit, history: dict, probes: dict) -> dict:
    v = _verification(lim, (rail.rail_id, "limit", lim.name), history)
    return {"name": lim.name, "value": lim.value, "unit": lim.unit,
            **_evidence(lim, v, _observation(rail.rail_id, lim, probes))}


def _concepts() -> list[dict]:
    """The shared vocabulary, each name with the rails that use it."""
    return [{"name": name, "definition": text,
             "rails": [rid for rid, rail in RAILS.items() if name in rail.capabilities]}
            for name, text in CONCEPTS.items()]


def _sources() -> list[dict]:
    """The committed documents the quotes were transcribed from, with their hashes."""
    out = []
    for url, rel in SOURCE_COPIES.items():
        data = (ROOT / rel).read_bytes()
        out.append({"url": url, "path": rel, "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data)})
    return out


def _stores(paths: list[Path]) -> list[dict]:
    """A checkpoint of every evidence stream: an older export pins the history behind it."""
    heads = []
    for path in paths:
        head = store.verify(path)
        if head.length:
            heads.append({"stream": path.stem, "length": head.length, "head": head.hash})
    return heads


def build(store_dir: Path | None = None) -> dict:
    """The registry as a JSON-able dict.

    `store_dir` names the evidence directory to read (tests pass a temporary one); left as None
    the committed `docs/observations/store/` is used. Every stream in it is checkpointed.
    """
    watch_path = store.stream_path(watch.STREAM, store_dir)
    history = watch.history(watch_path) if watch_path.exists() else {}
    probes = {rid: runner.history(p) for rid in RAILS
              if (p := store.stream_path(runner.stream_for(rid), store_dir)).exists()}
    paths = [store.stream_path(name, store_dir) for name in store.streams(store_dir)]
    rails = [
        {
            "rail_id": rail.rail_id,
            "display_name": rail.display_name,
            "hyperswitch_connector": rail.hyperswitch_connector,
            "capabilities": [_capability(rail, c, history, probes) for c in rail.capabilities.values()],
            "limits": [_limit(rail, l, history, probes) for l in rail.limits.values()],
        }
        for rail in RAILS.values()
    ]
    rows = [row for r in rails for row in (*r["capabilities"], *r["limits"])]
    dates = [row["obtained_on"] for row in rows if row["obtained_on"]]
    dates += [row["verification"]["checked_on"] for row in rows if row["verification"]]
    dates += [row["observation"]["latest"]["observed_on"] for row in rows if row["observation"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": max(dates) if dates else None,
        "tiers": [{"tier": t.value, "usable_as_fact": t.is_fact, "meaning": t.meaning}
                  for t in SourceTier],
        "concepts": _concepts(),
        "overlays": {"hyperswitch": dict(HYPERSWITCH)},
        "sources": _sources(),
        "stores": _stores(paths),
        "rails": rails,
    }


def render(store_dir: Path | None = None) -> str:
    return json.dumps(build(store_dir), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(render(), encoding="utf-8")
    SCHEMA_PATH.write_text(json.dumps(schema(), indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    doc = build()
    rows = sum(len(r["capabilities"]) for r in doc["rails"])
    print(f"wrote {REGISTRY_PATH.relative_to(ROOT)} ({len(doc['rails'])} rails, {rows} capabilities, "
          f"as of {doc['as_of']}) and {SCHEMA_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
