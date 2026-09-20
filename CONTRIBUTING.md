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
2. Fill `Capability` with a verbatim `quote`, the URL, and the date you read it.
3. Anything you could not verify is `UNVERIFIED`, never omitted.
4. Add a test that the engine refuses what the rail does not allow.
5. Regenerate `docs/RAIL_SEMANTICS.md`; CI fails on any drift.

## Licence

By contributing you agree your contribution is licensed under the Apache License 2.0 (see
`LICENSE`), as section 5 of that licence provides.
