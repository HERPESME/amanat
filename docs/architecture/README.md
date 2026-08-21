# Architecture

| Diagram | Level | Read it for |
|---|---|---|
| [c4-context.md](c4-context.md) | 1 | Why the system exists; who pays when each edge fails |
| [c4-containers.md](c4-containers.md) | 2 | The three-layer authority model |
| [c4-dynamic-settlement.md](c4-dynamic-settlement.md) | Dynamic | One cab ride end to end, including the refusal |
| [../RAIL_SEMANTICS.md](../RAIL_SEMANTICS.md) | — | Generated: what each rail is *evidenced* to permit |

No component-level diagram. The container diagram already names every module,
and at this size a component split would add boxes without adding information.

## The one-sentence version

> The LLM proposes. The policy engine disposes. The rail enforces.

Three layers of authority, each stricter than the last, and the outermost is a bank.

## The three invariants

1. **No path to money skips policy.** The orchestrator holds no authority; it can
   only call `AgentSession` methods, all of which route through
   `PolicyEngine.evaluate`. Proven without a model in
   `tests/test_session.py::test_a_refused_proposal_never_reaches_the_rail`.
2. **Absence of evidence is not permission.** A rail capability marked
   `UNVERIFIED` returns `False` from `permits()` even when `supported=True`. This
   currently refuses the project's *own core mechanism* on SBMD, because partial
   debit there rests on vendor docs rather than the NPCI circular.
3. **Refusals are evidence.** A denial is signed into the chain as carefully as a
   debit. A chain of happy paths proves nothing about governance.

## What is deliberately absent

- No LLM in the enforcement path — `policy/` is a pure function.
- No blockchain. Generic tamper-evident logging is heavily prior-arted
  (SCITT, in-toto, C2PA, and a wall of DLT patents). The claim is about *what* is
  chained — rail state transitions, including refused ones — never *how*.
- No float anywhere near money. Integer paise only.
- No risk model of our own. Risk enters through a seam; Razorpay ships RTO Shield.
