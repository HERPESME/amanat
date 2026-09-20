# Telling a vendor before publishing

A measurement can read as an accusation. This registry records what a vendor's documentation says and
what its sandbox did, on a date. Anything that could be read as a bug, an inconsistency or a gap in a
vendor's product reaches that vendor **before** it is published.

## What triggers it

1. A sandbox behaves differently from what the vendor documents.
2. Two pages of the vendor's documentation disagree.
3. A documented behaviour could not be reproduced.
4. Anything security-relevant. That goes to the vendor's security contact and is **not** published, in a
   report or anywhere else, until they say it is fixed or ten days after a second attempt to reach them.

A question the documentation leaves open (for example, what becomes of the uncaptured remainder after a
partial capture) is not a defect, but it is worth asking; asking is part of the measurement.

## What is sent

- The observation, in a paragraph, with its date and whether it was a sandbox or a live service.
- How to reproduce it: the command (`python -m amanat.probes run <probe>`) and the evidence hash of the
  stored run, so the exchange can be read line by line in `docs/observations/store/`.
- The exact wording that would be published, and where.
- An offer to correct anything they say is wrong, and to add their response beside the observation.

## Timeline

Fourteen days before publishing, longer on request if they are working on it. An observation that is
already public elsewhere does not wait. A vendor's reply, or a fix, is recorded with its date next to
the observation; a correction is marked as one, not silently edited.

## What is never done

- Probing production, moving real money, or using credentials that are not the project's own.
- Publishing a credential or a session token. The recorder redacts them; a test plants one and checks
  it never reaches the store.
- Describing intent. A sandbox that returns the same reference every time is reported as doing so.

## Template

> Subject: An observation about <product>'s <sandbox/documentation>, and a question
>
> Hello — I maintain an open-source registry of what payment rails do when an agent holds money
> (github.com/HERPESME/amanat). On <date> I observed <one sentence>. The recorded exchange is
> <evidence hash>, reproducible with `<command>`. The relevant page says "<verbatim sentence>".
> I plan to publish the observation, dated and marked as a <sandbox/live> result, on <date>, worded as
> above. If it is wrong, or the behaviour is intended, tell me and I will correct it or add your
> explanation beside it. I have not tested anything against production.

## Candidates, as of 2026-09-21

Nothing has been sent. These are the items in the current data that would need this step before a
report is published.

1. **Cashfree sandbox: a constant capture reference.** On every hold that saw a successful capture, the
   capture carries `action_reference: CAP_12121` (and every voided hold carries `VOID_12121`), and a second
   capture is refused with "Duplicate capture_id present". The outcome matches
   the documented rule that a transaction can only be captured or voided once; the wording, and the
   constant reference, look like a sandbox artefact. Evidence:
   `docs/observations/store/probes.cashfree_preauth.jsonl`.
2. **Cashfree: the uncaptured remainder.** The pre-authorisation guide says an authorisation not
   captured within seven days is released and does not say what becomes of the remainder of a partial
   capture. A dated measurement is running; the question, not a defect, is what to ask.
3. **Stripe: two pages that read differently.** The capture API reference says the amount to capture
   "must be less than or equal to the original amount", while the overcapture page says "Overcapture
   allows you to capture with an amount that’s higher than the authorized amount for a card payment."
   The second is an opt-in with eligibility conditions, so this is a wording gap rather than an error.
4. **Setu: two documented hosts that did not resolve.** On 21 Aug 2026 the UMAP quickstart named
   `uatapi.setu.co` (sandbox) and `api.setu.co` (production), and neither resolved in public DNS from two
   resolvers, while `accountservice.setu.co` and `bridge.setu.co` did. The documented behaviour could not be
   reproduced (trigger 3). Re-run on 21 Sep 2026 before anything is sent: NXDOMAIN has become NOERROR with no
   address record, from both resolvers, and a name that cannot exist answers the same way, so the finding
   stands as "no address record", not as NXDOMAIN; neither host is reachable by HTTPS from here. The likeliest
   explanation is an allowlist or private DNS rather than an error, and DNS alone does not show it. It is
   recorded as an observation made by hand: the answer is not in the evidence store.

## Sources that carry a notice

Visa's *Authorization and Reversal Processing Requirements for Merchants* is hosted publicly by Visa, and
its last page says the information is proprietary and confidential to Visa and must not be published or
disclosed in whole or in part without written permission. The registry quotes it in short, attributed
sentences and does not commit a copy. Whether that is acceptable is a decision for the owner of this
repository: ask Visa, or remove the `visa_card_auth` rail and regenerate. Until that is decided, the Visa
quotations come out on request, without discussion.
