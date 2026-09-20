# Security policy

Amanat is pre-1.0 software maintained by one person. It is a reference implementation and a
measurement project, not a production payment system: it holds no funds, custodies no
credentials, and talks only to sandboxes. Please report anything that would let it mislead a
reader about what money did.

## Reporting a vulnerability

Email **eeshan.singh53@gmail.com** with the subject `amanat security`. Include what you found,
how to reproduce it, and what you think it allows. Please do not open a public issue for
something exploitable.

You will get an acknowledgement within five working days — that is a solo maintainer's honest
promise, not an SLA. Fixes are made test-first, credited if you wish, and described in the
commit that lands them.

## What counts

Reports are most useful when they show a way to:

* make the policy engine allow an action its envelope or the rail's capability table forbids;
* move money without an evidence entry, or record an entry that misstates what happened;
* forge, truncate or re-order an evidence packet so that it still verifies **when pinned** to a
  trusted key or checkpoint (see below), or make a genuine packet fail to verify;
* make the verifier page or the console execute attacker-controlled script, or leak a key;
* get a credential read from `.env` into a log, a response or a packet.

## Documented limits (these are not vulnerabilities)

* **Unpinned verification proves internal consistency only.** A packet embeds the public key it
  was signed with, so anyone can mint one with a fresh key, and entries removed from the end are
  invisible without a checkpoint. `EvidenceChain.verify_packet` documents this and offers
  `trusted_keys` and `checkpoint` to bind a packet to a party; the verifier page says the same.
  Anchoring checkpoints with independent witnesses is planned, not done.
* **Sandbox results are the sandbox's.** Cashfree's authorisation is forced with
  `POST /simulate`; an `OBSERVED` capability means the sandbox API said so, not that an issuer did.
* **The console is a demonstration.** It runs a simulator; its human key is generated in your
  browser and is not persisted.

## Testing against real systems

This code refuses non-sandbox hosts by construction (see `rails/cashfree.py`, `rails/razorpay.py`).
Please do not point it, or a modified copy, at production payment credentials. If you research
a payment provider using this project, follow that provider's terms and rate limits.
