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
    RAILS, SOURCE_COPIES, Capability, Limit, RailProfile, SourceTier,
)
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
    """When this row's quote was last re-checked against its source, and how that has gone."""
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


def _evidence(row: Capability | Limit, verification: dict | None) -> dict:
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
    }


def _capability(rail: RailProfile, cap: Capability, history: dict) -> dict:
    v = _verification(cap, (rail.rail_id, "capability", cap.name), history)
    return {"name": cap.name, "supported": cap.supported,
            "permitted": rail.permits(cap.name), **_evidence(cap, v)}


def _limit(rail: RailProfile, lim: Limit, history: dict) -> dict:
    v = _verification(lim, (rail.rail_id, "limit", lim.name), history)
    return {"name": lim.name, "value": lim.value, "unit": lim.unit, **_evidence(lim, v)}


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


def build(watch_path: Path | None = None) -> dict:
    """The registry as a JSON-able dict.

    `watch_path` names the quote-check stream to read (tests pass a temporary one). Left as
    None, the committed streams are used and every stream is checkpointed.
    """
    history = watch.history(watch_path) if watch_path or store.stream_path(watch.STREAM).exists() else {}
    paths = [watch_path] if watch_path else [store.stream_path(name) for name in store.streams()]
    rails = [
        {
            "rail_id": rail.rail_id,
            "display_name": rail.display_name,
            "capabilities": [_capability(rail, c, history) for c in rail.capabilities.values()],
            "limits": [_limit(rail, l, history) for l in rail.limits.values()],
        }
        for rail in RAILS.values()
    ]
    rows = [row for r in rails for row in (*r["capabilities"], *r["limits"])]
    dates = [row["obtained_on"] for row in rows if row["obtained_on"]]
    dates += [row["verification"]["checked_on"] for row in rows if row["verification"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": max(dates) if dates else None,
        "tiers": [{"tier": t.value, "usable_as_fact": t.is_fact, "meaning": t.meaning}
                  for t in SourceTier],
        "sources": _sources(),
        "stores": _stores(paths),
        "rails": rails,
    }


def render(watch_path: Path | None = None) -> str:
    return json.dumps(build(watch_path), indent=2, ensure_ascii=False) + "\n"


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
