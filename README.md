# CausaTrace

> An evidence-first causal reproducibility debugger for Python experiments.

[![CI](https://github.com/Aryanbhargava18/CausaTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/Aryanbhargava18/CausaTrace/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Aryanbhargava18/CausaTrace)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12.8-3776AB?logo=python&logoColor=white)](.python-version)

When a Python experiment changes between runs, CausaTrace helps answer three
practical questions:

1. **Which tested runtime factor changed the result?**
2. **Where did that behavior enter the program?**
3. **Did the repair restore reproducibility?**

It investigates hidden execution factors such as hash randomization, random
number generators, filesystem ordering, and timezone. Rather than inferring a
cause from one trace, it runs controlled baseline and treatment trials,
records the evidence, and reports when the evidence is insufficient.

## What it does

- Establishes a repeated, pinned baseline before making a causal claim.
- Intervenes on one runtime factor at a time using paired trials.
- Compares stdout, stderr, exit status, artifacts, and numeric metrics.
- Combines runtime locations with clearly-labelled static heuristics.
- Suggests small source repairs and verifies them across repeated runs.
- Produces terminal, JSON, and self-contained HTML reports.
- Runs locally and offline after dependencies are installed.

## Quick start

Requirements: Python **3.12.8** and [`uv`](https://docs.astral.sh/uv/).

Install the locked development environment:

```bash
uv sync --locked --all-groups
```

Run the included demonstration:

```bash
make demo
```

The demonstration uses a small set-order experiment. It changes
`PYTHONHASHSEED` in controlled trials, identifies the resulting output change,
shows the source location, verifies a stable control, and deliberately rejects
an unrepaired `random.random()` script. That expected rejection is important:
verification must not hide nondeterminism by applying CausaTrace's own seeds.

Run the test suite:

```bash
make test
```

## Typical workflow

Analyze a Python script and choose a report format:

```bash
uv run --offline causatrace analyze path/to/script.py --project-root .
uv run --offline causatrace analyze path/to/script.py --format json > report.json
uv run --offline causatrace analyze path/to/script.py --format html --output report.html
```

Pass arguments to the target after `--`:

```bash
uv run --offline causatrace analyze path/to/script.py \
  --project-root . -- input.csv
```

Preview a repair, apply it explicitly, then verify the result:

```bash
uv run --offline causatrace fix path/to/script.py
uv run --offline causatrace fix path/to/script.py --write
uv run --offline causatrace verify path/to/script.py --project-root .
```

`fix` is preview-only by default. `--write` is explicit; if verification fails,
the original source is restored. Verification requires five successful runs
with identical stdout, stderr, and requested artifact hashes.

## Evidence model

Each analysis follows the same controlled protocol:

1. **Baseline stability** — three pinned baseline runs must agree. If they do
   not, CausaTrace reports `baseline_unstable` and withholds attribution.
2. **Paired intervention** — each factor is tested through three baseline and
   treatment pairs.
3. **Treatment stability** — a treatment must produce a repeatable difference;
   a one-off change is not enough.
4. **Localization** — intercepted operations and bounded call stacks provide
   runtime locations. AST findings are labelled heuristic and are not treated
   as runtime proof.
5. **Audit output** — reports include factor profiles, hashes, metrics, exit
   status, timeout state, artifact state, and per-trial evidence without copying
   raw program output or secrets.

The result is deliberately narrow: changing factor **F** under the declared
protocol repeatedly changed output **O**. It is not a claim of universal causal
inference or a guarantee that untested factors are safe.

## Example result

```text
hash_seed [baseline:hash_seed]: factor_supported
Evidence: stdout SHA-256
Runtime location: tests/fixtures/hash_dependent_set.py:4

clean control: PASS (1 output hash)
unrepaired random script: EXPECTED FAIL (5 output hashes)
```

## Supported factors

| Factor | What is varied |
|---|---|
| `PYTHONHASHSEED` | Hash-dependent ordering in a fresh interpreter |
| Python random | Module-level Python RNG state |
| NumPy random | Legacy generator and unseeded `default_rng()` when available |
| Filesystem listing | Supported listing hooks and their ordering |
| Timezone | `UTC`, `Asia/Kolkata`, `America/Los_Angeles`, and `Pacific/Kiritimati` |
| All released | An interaction profile that releases the tested controls together |

The default verification profile is intentionally unmasked: it uses UTC,
C.UTF-8, a different hash seed, shuffled listing order, and a minimal declared
environment, but does not seed Python or NumPy randomness.

## Additional commands

```bash
uv run --offline causatrace pytest --project-root .
uv run --offline causatrace junit results.xml --format json
uv run --offline causatrace compare-csv baseline.csv treatment.csv --key id
uv run --offline causatrace benchmark --project-root .
```

These commands support common data and test workflows without requiring a
service, account, or API key.

## Reports and configuration

The CLI supports terminal, JSON, and self-contained HTML reports. JSON and HTML
reports are designed for review and archiving: they contain hashes, metrics,
factor classifications, source locations, and limitations rather than raw
program output.

CausaTrace requires no runtime secrets. `.env.example` is included as a safe
configuration template, and real `.env` files are ignored by Git.

## Reproducible development

The repository pins Python 3.12.8 in `.python-version` and commits `uv.lock`.
The standard checks are:

```bash
make test
make demo
```

For a clean-machine boundary, build and run the Docker image with networking
disabled:

```bash
docker build -t causatrace .
docker run --rm --network none causatrace
docker run --rm --network none causatrace make demo
docker run --rm --network none causatrace \
  python tests/docker/timezone_probe.py
```

The image uses Python 3.12 slim, installs `tzdata` and `make`, and installs the
locked environment. The container test runs without network access; the image
build may download its pinned base and build inputs.

## Where it fits

CausaTrace complements tools that observe or record known inputs:

- Test frameworks find failures and counterexamples.
- Experiment trackers record declared parameters, metrics, and runs.
- Environment capture tools package dependencies and execution context.

CausaTrace focuses on the missing step: controlled intervention on hidden
runtime factors and repeated evidence for the resulting explanation.

## Scope and limitations

- Python scripts and the top-level process are supported. Child-process tracing
  is intentionally unsupported and reported as a limitation.
- Local analysis is not a network or filesystem security sandbox. Use Docker
  with `--network none` for a clean-machine boundary.
- The factor inventory is finite. `factor_not_observed` means “not observed
  under the tested controls,” not “impossible in all environments.”
- GPU/CUDA, notebooks, distributed jobs, additional languages, and arbitrary
  third-party RNGs are out of scope.
- Fixture benchmarks measure planted cases, not real-world accuracy.

## Project layout

```text
src/causatrace/   runner, probes, shim, attribution, reports, fixes
tests/            unit tests, integration tests, Docker probe, planted fixtures
Dockerfile        reproducible slim image with tzdata and offline test command
Makefile          test, demo, and repository checks
uv.lock           committed dependency lock
```

## License

MIT. See [LICENSE](LICENSE).
