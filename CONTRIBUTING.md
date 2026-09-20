# Contributing

Thanks for looking. This project is small and opinionated; the opinions are the point.

## Set up and run

```bash
git clone https://github.com/HERPESME/amanat && cd amanat
uv run --extra ml --extra web --extra dev pytest tests/ -q     # the whole suite: no credentials, no network
uv run --extra dev python -m amanat.demo                        # the end-to-end walkthrough
uv run --extra dev python -m amanat.rails.docgen                # regenerate docs/RAIL_SEMANTICS.md
```

Node.js is needed for a few tests: the verifier page's JavaScript is executed under Node and
compared with Python byte for byte. CI fails, rather than skips, if Node is missing.

## The rules that make the project what it is

1. **No model call in `policy/`, ever.** The model proposes; a deterministic engine disposes.
2. **Cited or refused.** A rail capability carries a verbatim quote from a source you read, and its
   tier (`PRIMARY`, `OBSERVED`, `SECONDARY`, `MARKETING`, `UNVERIFIED`). If you cannot quote it,
   it is `UNVERIFIED` and the engine refuses it. Absence of evidence is not permission.
3. **Refusals are evidence.** Every denial is written to the chain with its reason.
4. **Integer paise only.** No float touches money. The chain refuses a float outright.
5. **Say only what was measured.** No "first", "novel" or "nobody has built this". Name existing
   work, and state what a result does not show. If a claim would need an inference to hold, mark it
   unverified instead. The Cashfree "auto-released" episode (see `README.md`) is the cautionary tale.
6. **Test first.** A change to behaviour arrives with a test that failed before it. Tests exercise
   real code: the browser verifier is tested by running its real source, not a port of it.

## Adding or changing a rail capability

1. Find the primary source and read it — do not work from a summary.
2. Fill `Capability` with a verbatim `quote`, the URL of the page that carries it, and `obtained_on`
   (the ISO date you read it). Where the page has something between two sentences you quote, mark the
   gap with `…`; each piece must be verbatim. An `OBSERVED` row also names its `environment`
   (`sandbox` or `live`).
3. Anything you could not verify is `UNVERIFIED`, never omitted.
4. Add a test that the engine refuses what the rail does not allow.
5. Run `python -m amanat.registry.watch`: it fetches every cited page and checks each quote is
   present, and appends the run to `docs/observations/store/watch.jsonl`. A quote it cannot find is
   a defect in the row, not in the tool; commit the run with your change. (It needs the network, so
   CI does not run it — CI checks that a quote you edited was checked again.)
6. Regenerate `docs/RAIL_SEMANTICS.md` and `docs/registry/` (`python -m amanat.rails.docgen`,
   `python -m amanat.registry.export`); CI fails on any drift.

A model may propose a row; it is admitted only if the watcher finds its quote on the page. The check,
not the model, decides.

## Correcting a row about your product

The registry says things about other people's products, dated and cited. If a row about yours is wrong, out
of date or unfair, open an issue titled `row: <rail_id>.<capability>` (there is a
[template](.github/ISSUE_TEMPLATE/row-correction.md)) with the sentence you would put instead and where it
is written. A public page is best, because the watcher can then re-read it and the row can cite it; a private
source is welcome too, and the row will say that a public source cannot check it and that you can confirm or
correct it. Corrections are dated and marked as corrections, never silently edited. If you want your reply
recorded beside the observation, say so.

Anything that reads as a bug or an inconsistency in a vendor's product reaches that vendor before it is
published: see [`docs/reports/VENDOR-NOTIFICATION.md`](docs/reports/VENDOR-NOTIFICATION.md).

## Licence

By contributing you agree your contribution is licensed under the Apache License 2.0 (see
`LICENSE`), as section 5 of that licence provides.
