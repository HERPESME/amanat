# System Context — Amanat

Who touches the system, and why it exists at all.

The framing problem: **an agent must commit to a spending ceiling before the final
amount exists.** A human in a cab never does this — they watch the meter and pay
what it says. An agent has to name a number up front, and both directions of
error cost someone money.

```mermaid
C4Context
  title System Context - Amanat

  Person(human, "Customer", "States intent and a budget; can revoke at any time")
  Person_Ext(disputer, "Dispute Reviewer", "Bank, PSP or ombudsman assessing a contested debit")

  System(amanat, "Amanat", "Blocks a ceiling, debits the actual amount, releases the difference - and signs every rail state transition")

  System_Ext(llm, "Claude API", "Proposes actions; never touches money directly")
  System_Ext(rail, "Payment Rail", "UPI SBMD / OTM / card pre-auth. Holds and moves the money")
  System_Ext(merchant, "Merchant", "Reports the realised amount once it is known")

  Rel(human, amanat, "States intent and budget")
  Rel(amanat, llm, "Asks for a proposed action", "HTTPS")
  Rel(amanat, rail, "Reserves, debits, releases, revokes", "PSP API")
  Rel(human, rail, "Revokes the block directly", "own UPI app")
  Rel(merchant, amanat, "Reports realised amount")
  Rel(amanat, disputer, "Produces a signed evidence packet")

  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

## What each relationship costs if it goes wrong

| Relationship | Failure | Who pays |
|---|---|---|
| Customer → Amanat | Budget too tight for the real fare | Sale is lost |
| Amanat → Rail (reserve) | Ceiling set too low | Debit rejected, sale lost |
| Amanat → Rail (reserve) | Ceiling set too high | Customer's money stranded |
| Customer → Rail (revoke) | Customer revokes mid-delivery | Merchant has shipped, cannot debit |
| Amanat → Dispute Reviewer | No evidence of what the money did | Merchant loses the representment |

The last row is the one nothing else on the market covers. Every shipping
agent-payment evidence standard terminates at **authorization** — it proves the
agent was *permitted* to spend. None of them records what the money subsequently
*did*.

## Deliberate non-goals

- **Not a fraud/RTO risk engine.** Razorpay ships RTO Shield and risk-tiered COD
  fees already. Risk enters through the `CeilingSource` seam (`ceiling/source.py`), never re-implemented.
- **Not a negotiation layer.** Measured surplus for instrument-negotiation is ~zero:
  merchants already publish unconditional prepaid incentives, and the merchant knows
  more about the buyer's risk than the buyer's agent does.
- **Not a new rail.** Amanat is strictly a consumer of rails it can evidence.
