# Dynamic Diagram — Amount-Contingent Settlement

One cab ride, end to end, including the refusal. This is the flow the demo walks
through (`python -m amanat.demo`).

```mermaid
C4Dynamic
  title Dynamic Diagram - Amount-Contingent Settlement

  Person(human, "Customer", "Budget 600 rupees")
  System_Ext(llm, "Claude API", "Proposes")
  System_Ext(rail_ext, "Payment Rail", "Holds the money")

  Container_Boundary(amanat, "Amanat") {
    Container(session, "Agent Session", "Python", "The only path to money")
    Container(policy, "Policy Engine", "Python", "Deterministic verdict")
    Container(ceiling, "Ceiling Model", "conformal", "Quantile of the fare distribution")
    ContainerDb(chain, "Evidence Chain", "Ed25519", "Signs every transition")
  }

  Rel(human, session, "1. Envelope: max 600, payee citycabs, 6h")
  Rel(ceiling, llm, "2. p95 of predicted fare = 620")
  Rel(llm, session, "3. Propose RESERVE 620")
  Rel(session, policy, "4. Evaluate against envelope")
  Rel(policy, chain, "5. REFUSED - exceeds 600 budget")
  Rel(llm, session, "6. Propose RESERVE 600")
  Rel(session, rail_ext, "7. Block 600 in customer account")
  Rel(rail_ext, chain, "8. Sign transition BLOCKED")
  Rel(llm, session, "9. Propose DEBIT 470 - actual fare")
  Rel(session, rail_ext, "10. Partial debit 470")
  Rel(session, rail_ext, "11. Release remaining 130")
  Rel(rail_ext, chain, "12. Sign DEBITED then RELEASED")

  UpdateRelStyle(policy, chain, $textColor="red", $lineColor="red", $offsetY="-10")
  UpdateRelStyle(session, rail_ext, $textColor="green", $lineColor="green", $offsetX="-50")
  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

Step 5 is the interesting one. The model asked for the statistically correct
ceiling and the policy engine said no, because the human's budget is a harder
boundary than the model's confidence. The refusal is **signed into the evidence
chain**, so the dispute artifact later shows not only what happened but what was
prevented.

## The two failure modes, and why they are recorded differently

| Failure | Caught by | Why |
|---|---|---|
| Ceiling above the envelope budget | **Policy** | Locally knowable — refuse before spending a rail call |
| Debit above the standing block | **Policy** | Locally knowable — the engine tracks block state |
| Customer revokes mid-delivery | **Rail** | Not locally knowable. The customer acted out of band |
| Block expired / bank down | **Rail** | Not locally knowable |

A dispute needs to distinguish *"we refused"* from *"the bank refused"* — they
assign liability to different parties. Test:
`tests/test_session.py::test_the_two_failure_kinds_are_separable_in_the_audit_trail`.

## The graceful-failure demo (Act 4)

Under-set ceiling → debit rejected → **3 retries within 24h, the retry budget
NPCI OC-228 defines** → exhausted → block released → sale lost. Then the same
order at p95 succeeds, with the stranded amount shown.

One failure, a rail-defined retry budget, and a quantified tradeoff — rather than
a generic try/except.
