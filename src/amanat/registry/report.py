"""Rail Semantics Report: a dated snapshot of what the registry holds, generated from it.

    python -m amanat.registry.report        # writes docs/reports/rail-semantics-report-1.md

Every number and every list in the report is computed from the exported registry and the evidence
streams behind it, so a statement in it cannot outlive the data it rests on: when a row changes,
the report changes with it, and CI fails until the file is regenerated. The prose around the
computed parts says only what the method supports — a snapshot, a sandbox is not an issuer, an
unverified row is a refusal — and claims no priority.

The report is a *draft for a person to publish*. Publishing it, and telling the vendors it discusses
first, is outside what this code does; docs/reports/VENDOR-NOTIFICATION.md says how.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from amanat.registry import export, page, store

OUT = export.ROOT / "docs" / "reports" / "rail-semantics-report-1.md"
# The questions the ceiling mechanism asks of a rail, in the order a reader meets them.
HEADLINE = ["funds_held_in_customer_account", "partial_debit", "over_capture", "multiple_captures",
            "partial_void", "void_after_partial_capture", "void_whole_hold", "remainder_auto_released",
            "idempotent_replay"]


def _verdict(row: dict) -> str:
    return page._verdict(row)


def _label(rail_id: str, row: dict) -> str:
    tier = row["tier"] + (f", {row['environment']}" if row["environment"] else "")
    return f"{page.SHORT[rail_id]} ({tier})"


# What each group of rails is, in a sentence, so that a count over "rails" is never read as a count of
# independent systems. A new group must be described (a test enforces it).
GROUP_KIND = {
    "UPI and Indian PSPs": "UPI and Indian PSP products, the ones this repository has adapters or engine rules for",
    "Card networks and PSPs": "card-payment documents: Visa's own guide and two acquirers' documentation, which describe one "
                              "card mechanism from the network's side and from two acquirers' sides",
    "Agent-payment protocol: x402": "specifications of one protocol, x402 (five schemes and one set of extensions), each read at "
                                    "a pinned revision",
}
_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def _composition(doc: dict) -> str:
    present = {r["rail_id"] for r in doc["rails"]}
    parts = [f"{sum(1 for i in ids if i in present)} are {GROUP_KIND[name]}" for name, ids in page.GROUPS]
    kinds = _NUMBER_WORDS.get(len(page.GROUPS), str(len(page.GROUPS)))
    return (f"The rails are not equals, and a count over them mixes {kinds} kinds: " + "; ".join(parts) + ". "
            "A count of rails is a count of documents, not of independent systems: read the lists, not only the totals.")


def _by_kind(doc: dict, concept: str) -> str:
    """The same question answered group by group, so a total is never the whole story."""
    out = []
    for name, ids in page.GROUPS:
        tally = Counter(_verdict(row) for rail in doc["rails"] if rail["rail_id"] in ids
                        for row in rail["capabilities"] if row["name"] == concept)
        if tally:
            bits = [f"{tally['yes']} supported"] + ([f"{tally['no']} refused"] if tally["no"] else []) \
                + ([f"{tally['unk']} unverified"] if tally["unk"] else [])
            out.append(f"{name}: " + ", ".join(bits))
    return "; ".join(out)


def _hand_observed(doc: dict) -> str:
    """The observations made by hand: what they were, when, and on what."""
    bits = []
    for rail in doc["rails"]:
        for row in (*rail["capabilities"], *rail["limits"]):
            if row["tier"] == "observed" and not row["observation"]:
                bits.append(f"{page.SHORT[rail['rail_id']]}'s {row['name'].replace('_', ' ')}, "
                            f"{row['obtained_on']}, {row['environment']}")
    return "; ".join(bits)


def _by_concept(doc: dict, concept: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"yes": [], "no": [], "unk": []}
    for rail in doc["rails"]:
        for row in rail["capabilities"]:
            if row["name"] == concept:
                out[_verdict(row)].append(_label(rail["rail_id"], row))
    return out


def _join(items: list[str]) -> str:
    return "; ".join(items) if items else "none"


def _concept_section(doc: dict) -> str:
    defs = {c["name"]: c["definition"] for c in doc["concepts"]}
    parts = []
    for name in HEADLINE:
        groups = _by_concept(doc, name)
        answered = len(groups["yes"]) + len(groups["no"])
        with_row = {r["rail_id"] for r in doc["rails"] if any(c["name"] == name for c in r["capabilities"])}
        without = [page.SHORT[r["rail_id"]] for r in doc["rails"] if r["rail_id"] not in with_row]
        no_row = f"- **No row for this question** ({len(without)}): {_join(without)}\n" if without else ""
        parts.append(
            f"### `{name}`\n\n{defs[name]}\n\n"
            f"- **Supported** ({len(groups['yes'])}): {_join(groups['yes'])}\n"
            f"- **Not supported** ({len(groups['no'])}): {_join(groups['no'])}\n"
            f"- **Unverified — refused, not assumed** ({len(groups['unk'])}): {_join(groups['unk'])}\n"
            f"{no_row}\n"
            f"{answered} of {answered + len(groups['unk'])} rails with a row for this question have an answer that rests on evidence usable as fact.\n")
    return "\n".join(parts)


def _measured(doc: dict) -> str:
    rows = []
    for rail in doc["rails"]:
        for row in rail["capabilities"]:
            o = row["observation"]
            if o is None or row["tier"] != "observed":
                continue
            latest = o["latest"]
            verdict = "supported" if latest["supported"] else "not supported"
            words = row["quote"].replace("|", "\\|")
            rows.append(f"| `{rail['rail_id']}.{row['name']}` | {verdict} | {words} | {latest['observed_on']} | "
                        f"`{latest['evidence_hash'][:12]}` |")
    head = "| Row | Answer | The rail's words | Observed | Evidence |\n|---|---|---|---|---|\n"
    return head + "\n".join(rows)


def _reference_caveat(facts: dict) -> str:
    captured = {r for refs in facts["captured"].values() for r in refs}
    if len(captured) != 1:
        return ""
    (ref,) = captured
    voided = {r for refs in facts["voided"].values() for r in refs}
    void_part = (f", and each of the {len(facts['voided'])} voided holds carries {next(iter(voided))}"
                 if facts["voided"] and len(voided) == 1 else "")
    return (f"Caveat from the stored records: on each of the {len(facts['captured'])} holds that saw a successful capture, the capture "
            f"carries the same `action_reference` ({ref}){void_part}, so the wording of the refusal of a second capture is probably a "
            "sandbox artefact. The outcome agrees with the vendor's documented rule that a transaction is captured or voided once.")


def _regulator_note(unreadable: str) -> str:
    if "npci.org.in" not in unreadable:
        return ""
    return ("The regulator's site refuses scripted clients, so the two NPCI circulars the primary rows were transcribed from are "
            "committed and their hashes are exported. ")


def _limits_line(doc: dict) -> str:
    bits = []
    for rail in doc["rails"]:
        for lim in rail["limits"]:
            if lim["name"] == "hold_expiry_days":
                bits.append(f"{page.SHORT[rail['rail_id']]} {lim['value']} days")
    windows = [(page.SHORT[r["rail_id"]], lim["value"]) for r in doc["rails"] for lim in r["limits"]
               if lim["name"].startswith("hold_expiry_days_") and r["rail_id"] == "visa_card_auth"]
    if windows:
        bits.append(f"Visa {min(v for _, v in windows)}–{max(v for _, v in windows)} days by channel and merchant category")
    return ", ".join(bits)


# One plain sentence per unverified row, written for a reader who has not read the row's notes. A new
# unverified row must be given one here (a test enforces it): the report says what is not known.
UNVERIFIED_WHY = {
    ("sbmd", "block_amount_reducible_without_revoke"):
        "No source read says whether a standing block's amount may be lowered without revoking it: the PSP APIs bound a modified "
        "amount only by a minimum and a maximum, and the NPCI circulars name modification without stating a direction.",
    ("stripe_card_manual_capture", "payment_guarantee"):
        "Stripe says an authorisation \"guarantees the amount by holding it\". That is about reserving funds, not about the merchant "
        "being paid, so it is not read as a payment guarantee; NPCI's circular for Reserve Pay says outright that a block is not one.",
    ("upi_otm", "post_delivery_debit_goods"):
        "PayU's documentation says the merchant captures \"usually after the goods or services are delivered\", which conflicts with "
        "the debit-before-delivery rule in the NPCI circular for Reserve Pay; a PSP page cannot settle a rule of the rail.",
    ("cashfree_preauth", "remainder_auto_released"):
        "Cashfree documents that an authorisation not captured within seven days is released and does not say what becomes of the "
        "remainder of a partial capture; a dated measurement of the sandbox is running.",
    ("visa_card_auth", "over_capture"):
        "The guide's only route to a higher final amount is an incremental authorisation, and it is silent on clearing above the "
        "authorised sum.",
    ("stripe_card_manual_capture", "partial_void"):
        "Stripe documents no way to reduce an authorisation without capturing; the closest analogue is a capture of zero marked "
        "final, after at least one capture.",
}


def _unverified(doc: dict) -> str:
    lines = []
    for rail in doc["rails"]:
        for row in rail["capabilities"]:
            if row["tier"] == "unverified":
                key = (rail["rail_id"], row["name"])
                if key not in UNVERIFIED_WHY:
                    raise KeyError(f"{key[0]}.{key[1]} is unverified but the report has no sentence for it: "
                                   "add one to UNVERIFIED_WHY")
                lines.append(f"- `{key[0]}.{key[1]}` — {UNVERIFIED_WHY[key]}")
    return "\n".join(lines)


def _probe_facts(store_dir: Path | None) -> dict:
    """What the stored probe runs say about themselves.

    How many probes, how many distinct holds, and which action references each hold's successful
    capture (or void) carried. A payments listing repeats the reference of the capture it lists, so
    references are counted per hold, not per response.
    """
    probes, orders, refs = set(), set(), Counter()
    captured: dict[str, set[str]] = {}
    voided: dict[str, set[str]] = {}
    for name in store.streams(store_dir):
        if not name.startswith("probes."):
            continue
        for _, rec in store.records(store.stream_path(name, store_dir)):
            if rec["kind"] != "probe_run":
                continue
            probes.add(rec["data"]["probe_id"])
            order = None
            for ex in rec["data"]["exchanges"]:
                body = ex["response"]
                if ex["label"] == "order_create" and isinstance(body, dict) and body.get("order_id"):
                    order = body["order_id"]
                    orders.add(order)
                for item in (body if isinstance(body, list) else [body]):
                    auth = (item or {}).get("authorization") if isinstance(item, dict) else None
                    ref = (auth or {}).get("action_reference")
                    if not ref:
                        continue
                    refs[ref] += 1
                    where = {"CAPTURE": captured, "VOID": voided}.get(auth.get("action"))
                    if where is not None and order is not None:
                        where.setdefault(order, set()).add(ref)
    return {"probes": len(probes), "orders": len(orders), "refs": refs, "captured": captured, "voided": voided}


def _unreadable(doc: dict) -> str:
    hosts = Counter()
    for rail in doc["rails"]:
        for row in (*rail["capabilities"], *rail["limits"]):
            v = row["verification"]
            if v and v["result"] == "unfetchable":
                hosts[row["url"].split("/")[2] + f" ({v['detail']})"] += 1
    return "\n".join(f"- {n} rows cite `{h}`" for h, n in sorted(hosts.items())) or "- none"


def _measured_idempotency(doc: dict) -> str:
    rows = [f"{page.SHORT[r['rail_id']]}'s sandbox, on {row['name'].replace('_replay', '').replace('idempotent_', '')}"
            for r in doc["rails"] for row in r["capabilities"]
            if row["tier"] == "observed" and "idempotent" in row["name"]]
    return _join(rows)


_REFUSALS = {"duplicate_order_refused": "a repeated order creation", "payment_replay_refused": "a repeated payment",
             "idempotency_key_reuse_refused": "a reused key with a different request"}


def _measured_refusals(doc: dict) -> str:
    """What else a sandbox was measured to refuse on a retry, in the words of the rows that say so."""
    found = [_REFUSALS[row["name"]] for r in doc["rails"] for row in r["capabilities"]
             if row["name"] in _REFUSALS and row["tier"] == "observed" and row["supported"] is True and row["observation"]]
    if not found:
        return ""
    joined = found[0] if len(found) == 1 else ", ".join(found[:-1]) + " and " + found[-1]
    return f"; on the same sandbox {joined} {'was' if len(found) == 1 else 'were'} each refused"


def render(doc: dict | None = None, store_dir: Path | None = None) -> str:
    doc = doc if doc is not None else export.build(store_dir)
    facts = _probe_facts(store_dir)
    unreadable = _unreadable(doc)
    st = page._stats(doc)
    partial = _by_concept(doc, "partial_debit")
    idem = _by_concept(doc, "idempotent_replay")
    remainder = _by_concept(doc, "remainder_auto_released")
    hand = f" ({_hand_observed(doc)})" if st["by_hand"] else ""
    pinned = (f" ({st['pinned']} of them cite a source pinned to a revision, where re-reading shows that the quote was transcribed "
              f"correctly and can never show that anything changed; the other {st['reread'] - st['pinned']} cite pages that can change, "
              "and those are the ones the watcher guards)") if st["pinned"] else ""
    streams = "\n".join(f"| `{s['stream']}` | {s['length']} | `{s['head']}` |" for s in doc["stores"])
    docs = "\n".join(f"| `{s['path']}` | `{s['sha256']}` |" for s in doc["sources"])
    return f"""# Rail Semantics Report #1

*A dated snapshot of what payment rails say and do when an agent holds money, captures less than the hold, releases the rest, or retries. As of {doc["as_of"]}.*

Generated by `python -m amanat.registry.report` from [`docs/registry/registry.json`](../registry/registry.json) and the evidence
streams behind it. Every number and list below is computed from that data; the prose around them is deliberately plain. This is a
snapshot: pages change and sandboxes change, and the point of the registry is that the next run says so.

## What is in the registry

{st["rails"]} rails, {st["capabilities"]} capabilities, {st["limits"]} numeric limits. {st["probed"]} rows rest on recorded probe runs against a vendor's sandbox, each with its stored exchange;
{st["by_hand"]} more rest on one-off observations made by hand{hand}, whose exchanges are not in the evidence store.
{st["reread"]} quotes were **re-read** from the source they cite on the date above{pinned}; {st["unreadable"]} sources could not be read
(below); {st["unverified"]} capabilities are **unverified** and therefore refused by the policy engine rather than assumed.

How a row is admitted: it carries a verbatim quote and the page it came from, and `python -m amanat.registry.watch` fetches that page and
looks for the quote. A measured row names a probe, and the test suite fails if the rail's latest conclusive answer disagrees with the row.
Rows for the reference rails (Visa, Stripe, Adyen, x402) were proposed by agents that read the sources; each was admitted only because that
check found its quote, and where a source is silent the row is unverified.

## What the comparison shows

{_composition(doc)}

- **A smaller capture than the hold** is supported on {len(partial["yes"])} rails and refused on {len(partial["no"])}
  ({_join(partial["no"])}); {len(partial["unk"])} have no evidenced answer. By kind: {_by_kind(doc, "partial_debit")}.
- **Who gives the rest back, and when** differs more than whether it is given back: supported on
  {_join(remainder["yes"])}; refused on {_join(remainder["no"])}; unverified on {_join(remainder["unk"])}. In words: on UPI Reserve Pay nobody
  does, since the block stays until someone revokes it or its end date arrives, up to 90 days; on x402 `upto` on Solana the escrow does,
  in the same settlement; on Stripe and Adyen the acquirer cancels the unclaimed amount, neither page says when the cardholder's issuer frees
  the balance, and Adyen's holds only while multiple partial capture, which is off by default, stays off; on Visa the merchant's own
  reversal is what removes the hold, and the guide obliges the merchant to send it; on Cashfree nothing is established, so the engine
  refuses to plan around it.
- **A hold's life differs by rail, and so does what its deadline means**: {_limits_line(doc)}. Razorpay refunds an uncaptured payment after
  at most that long, an upper bound on a timeout the merchant sets, and the money then takes 5–7 working days to arrive; Cashfree's page says
  the funds are released; Adyen's own expiry leaves the payment neither capturable nor cancellable; Visa's figures are clearing deadlines, not
  releases: after them the merchant still owes a reversal, and Visa assesses a Misuse of Authorization System Fee on authorizations matched to
  neither a clearing nor a reversal. Only on some rails does the deadline end the agent's obligation.
- **A retry that acts once** is documented for {len(idem["yes"])} rails ({_join(idem["yes"])}); x402's is an optional extension, so a server
  that leaves it off is still conformant. Separately, measured: {_measured_idempotency(doc)}{_measured_refusals(doc)}.
- **Some questions have no answer yet**: {st["unverified"]} capabilities are unverified. They are listed below with what was read.

## Question by question

Each list gives the rail and the kind of evidence behind its answer. A sandbox is not an issuer: an *observed, sandbox* answer says what
the vendor's test environment did on the day.

{_concept_section(doc)}
## What was measured

{facts["probes"]} probes ran against Cashfree's UPI pre-authorisation sandbox, {facts["orders"]} holds in all, each answer stored with the
exchange that produced it. The authorisation is forced with `POST /simulate`, so this is the sandbox API's word.

{_measured(doc)}

{_reference_caveat(facts)}

## What could not be established

Unverified — refused by the engine, never assumed:

{_unverified(doc)}

Sources that could not be read on the date above (reported as unreadable, never as a missing quote):

{unreadable}

{_regulator_note(unreadable)}Visa's guide is treated as secondary because it says the Visa Rules govern in any conflict, and the Rules have not been read. It also carries a confidentiality notice on its last page although Visa hosts it publicly; this report and the registry quote it only in short, attributed sentences, and will remove them on request.

## Reproduce it

```bash
uv run --extra dev python -m amanat.registry.watch          # re-read every quote from its source
uv run --extra dev python -m amanat.probes run              # re-measure the Cashfree sandbox (needs sandbox credentials in .env)
uv run --extra dev python -m amanat.registry.export         # the registry, its schema and the page
uv run --extra dev python -m amanat.registry.report         # this file
```

Checkpoints of the evidence behind this report. A later export whose streams do not extend these has rewritten history.

| Stream | Lines | Head (SHA-256) |
|---|---|---|
{streams}

Source documents committed to the repository:

| File | SHA-256 |
|---|---|
{docs}

## Before this is published

Anything here that reads as a bug or an inconsistency in a vendor's product should reach that vendor first. See
[`VENDOR-NOTIFICATION.md`](VENDOR-NOTIFICATION.md).
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(export.ROOT)}")


if __name__ == "__main__":
    main()
