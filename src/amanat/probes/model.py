"""The probe vocabulary: steps, rules, exchanges, and what counts as an answer.

A probe is a list of named operations a harness knows how to perform against a rail (`hold`,
`capture`, `void`, ...) plus rules that read the recorded exchanges into findings about
capabilities. Both are data, so a run can print the exact question it asked and a vendor can
read it.

The one design rule: only an *answer to the question* is a finding. A 2xx or a semantic 4xx is
an answer. A 401 (wrong credentials), 403, 408, 429, any 5xx, a redirect, a timeout and a
failed precondition are not — they say nothing about what the rail permits, so they yield an
inconclusive finding and never flip a row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from amanat.rails.semantics import Environment

ANSWERS = ("2xx", "4xx")
_NOT_SEMANTIC = frozenset({401, 403, 408, 429})


def status_class(status: int | None) -> str:
    """`2xx`, `4xx` (a refusal of the request itself) or `inconclusive` (not an answer)."""
    if status is None:
        return "inconclusive"
    if 200 <= status < 300:
        return "2xx"
    if 400 <= status < 500 and status not in _NOT_SEMANTIC:
        return "4xx"
    return "inconclusive"


@dataclass(frozen=True)
class Ref:
    """A step argument that means "the handle step `step` returned"."""

    step: str


@dataclass(frozen=True)
class Step:
    name: str
    op: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """If step `step` answered this way, the rail does (or does not) support `capability`."""

    capability: str
    step: str
    supported: bool
    basis: str                        # a sentence; {status} and {message} are filled in
    status: str | None = None         # "2xx" | "4xx"; None accepts either answer
    label: str | None = None          # which exchange of the step; default: its last
    where: tuple[tuple[str, Any], ...] = ()   # (dotted.path, expected) checks on the response body
    after: tuple[tuple[str, str], ...] = ()   # (step, "2xx"|"4xx"): applies only if that step answered so


@dataclass(frozen=True)
class Probe:
    probe_id: str                     # "<rail_id>.<what>"
    rail_id: str
    environment: Environment
    steps: tuple[Step, ...]
    rules: tuple[Rule, ...]
    summary: str = ""

    def definition(self) -> dict:
        """The question, as data: what is sent, in what order, and how answers are read."""
        def arg(v):
            return {"ref": v.step} if isinstance(v, Ref) else v

        return {
            "steps": [{"name": s.name, "op": s.op, "args": {k: arg(v) for k, v in s.args.items()}}
                      for s in self.steps],
            "rules": [{"capability": r.capability, "step": r.step, "supported": r.supported,
                       "basis": r.basis, "status": r.status, "label": r.label,
                       "where": [[p, v] for p, v in r.where],
                       "after": [[st, k] for st, k in r.after]} for r in self.rules],
        }


@dataclass(frozen=True)
class Exchange:
    """One call and its answer, as recorded. Headers are never part of it."""

    label: str
    request: dict
    status: int | None
    response: Any
    error: str | None = None
    at: str = ""


@dataclass
class OpResult:
    exchanges: list[Exchange]
    handle: Any = None
    ok: bool = True                   # False: the operation could not be carried out


class Harness(Protocol):
    """What a rail must offer for probes to run against it."""

    rail_id: str
    environment: Environment
    name: str

    @property
    def ops(self) -> frozenset[str]: ...

    def run_op(self, op: str, args: dict) -> OpResult: ...
