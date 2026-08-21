# Container Diagram — Amanat

The three-layer authority model, which is the whole architecture:

> **The LLM proposes. The policy engine disposes. The rail enforces.**

Each layer is stricter than the one above it, and the outermost is a bank. There
is no code path from the model to the rail that skips the middle.

```mermaid
C4Container
  title Container Diagram - Amanat

  Person(human, "Customer", "Intent and budget")
  System_Ext(llm, "Claude API", "Tool-calling model")
  System_Ext(rail_ext, "Payment Rail", "UPI SBMD / OTM / pre-auth")
  Person_Ext(disputer, "Dispute Reviewer", "Verifies packets offline")

  Container_Boundary(amanat, "Amanat") {
    Container(agent, "Orchestrator", "Python, Anthropic SDK", "Binds governed actions as tools. Holds no authority of its own")
    Container(session, "Agent Session", "Python", "The single path to money: propose to policy to rail to evidence")
    Container(policy, "Policy Engine", "Python, pure function", "Deterministic. Checks envelope, then rail legality. No model call")
    Container(semantics, "Rail Semantics", "Python", "Machine-readable table of what each rail is EVIDENCED to permit")
    Container(rails, "Rail Adapters", "Python", "Simulator and PSP adapters behind one interface")
    Container(ceiling, "Ceiling Source", "scikit-learn, conformal", "Seam. Conformal model is the reference impl; production injects RTO Shield")
    ContainerDb(chain, "Evidence Chain", "Ed25519, SHA-256", "Append-only, hash-linked, signed. Records refusals too")
  }

  Rel(human, session, "Envelope: budget, payees, expiry")
  Rel(agent, llm, "Proposes an action", "HTTPS")
  Rel(agent, session, "Calls a governed tool")
  Rel(session, policy, "Submits proposal for verdict")
  Rel(policy, semantics, "Asks: is this legal on this rail?")
  Rel(session, rails, "Applies approved actions only")
  Rel(rails, rail_ext, "reserve / debit / release / revoke", "PSP API")
  Rel(ceiling, session, "Suggests a ceiling at quantile q")
  Rel(session, chain, "Appends every proposal, verdict and transition")
  Rel(chain, disputer, "Exports a self-verifying packet")

  UpdateRelStyle(session, policy, $textColor="red", $lineColor="red", $offsetY="-20")
  UpdateRelStyle(policy, semantics, $textColor="red", $lineColor="red", $offsetX="-40")
  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

The two red edges are the ones that carry the architecture. Everything else is
plumbing.

## Why the LLM is outside the trust boundary

The orchestrator has **no authority**. It can only call methods on `AgentSession`,
and every one of those routes through `PolicyEngine.evaluate` before reaching a
rail. A hallucinating, prompt-injected or simply wrong model produces a *refusal
record*, not a payment.

That property is testable without a model, and it is:
`tests/test_session.py::test_a_refused_proposal_never_reaches_the_rail`. If
proving the agent is bounded required running the agent, it would not be bounded.

## Why rail semantics is its own container

It is read by the policy engine *and* enforced by the simulator, so the two
cannot drift into disagreeing about what a rail permits. It also generates
`docs/RAIL_SEMANTICS.md`, so the prose cannot claim more than the runtime honours.

| Container | Needs network? | Needs an API key? |
|---|---|---|
| Orchestrator | yes | yes |
| Agent Session, Policy Engine, Rail Semantics, Evidence Chain | no | no |
| Ceiling Model | only to fetch TLC data once | no |
| Rail Adapters | simulator no; PSP adapters yes | PSP creds |
