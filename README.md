# CausaTrace

CausaTrace is an offline Python CLI for investigating why a script or experiment
changes its output across runs or machines. It establishes a pinned baseline,
changes one tested factor at a time, compares outputs, records supported source
locations, and reports only evidence-backed findings.

## Evaluator quickstart

The fastest way to see the core claim is one offline command:

```bash
make demo
```

The demo shows a real set-order output change, identifies `PYTHONHASHSEED` as
the tested factor, reports the source location, proves a clean control across
five runs, and deliberately rejects an unrepaired random script. A successful
demo exits zero; the random script's expected verification failure is part of
the proof that verification is not masking nondeterminism.

## Development setup

Python 3.12.8 and `uv` are required. The lockfile is committed so dependency
resolution is reproducible.

```bash
uv sync --locked --all-groups
make test
```

The test command is offline after the environment has been prepared. It uses
only local fixtures and does not call APIs or external services.

## Container verification

The Docker image installs `tzdata` and `make`, installs the locked project
environment, and defaults to the offline test command:

```bash
docker build -t causatrace .
docker run --rm --network none causatrace
```

The Docker build may download pinned build inputs. The container test command
must not download anything or access the network.

## Usage

The P0 command accepts a Python script and emits a readable terminal report or
machine-readable JSON. It runs a pinned baseline three times, then runs three
paired trials for each supported intervention. The report distinguishes a
stable baseline, baseline instability, factor-supported changes, suspected
interactions, and inconclusive experiments.

```bash
uv run --offline causatrace analyze path/to/script.py --project-root .
uv run --offline causatrace analyze path/to/script.py --format json > report.json
uv run --offline causatrace analyze path/to/script.py --format html --output report.html
uv run --offline causatrace verify path/to/script.py --project-root .
uv run --offline causatrace fix path/to/script.py
uv run --offline causatrace pytest --project-root .
uv run --offline causatrace benchmark --project-root .
uv run --offline causatrace demo --project-root .
```

Optional artifact hashes and numeric stream metrics can be requested with
`--artifact PATH` and repeated `--metric-regex REGEX` options. P0 uses exact
SHA-256 output comparisons; numeric tolerances, CSV row diffs, pytest/JUnit
results, verified codemods, and HTML reports are P1 work.

Pass script arguments after `--`:

```bash
uv run --offline causatrace analyze path/to/script.py --project-root . -- input.csv
```

The supported P0 interventions are:

- `PYTHONHASHSEED` with a fresh interpreter
- Python's module-level random generator
- NumPy's legacy generator and unseeded `default_rng()` when NumPy is present
- filesystem listing order through supported listing hooks
- `UTC`, `Asia/Kolkata`, `America/Los_Angeles`, and `Pacific/Kiritimati`
- one all-released profile to flag possible interactions

Static AST findings are labelled heuristic and are never runtime proof. Runtime
locations are reported only for intercepted operations whose first relevant
frame is in the target project.

## Scope and limitations

- P0 supports Python scripts and the top-level process only. Instrumentation of
  child processes is intentionally deferred and will be reported as a
  limitation rather than implied.
- P0 tests hash seeds, Python and available NumPy randomness, filesystem
  listing order, timezone, and an all-released profile. It does not claim to
  control every source of nondeterminism.
- A causal finding requires a stable pinned baseline and repeated controlled
  interventions. A plausible static pattern is never treated as runtime proof.
- Runtime locations are limited to supported intercepted operations and the
  first relevant user-code frame. Standard-library and C-extension frames are
  kept distinct.
- P0 repair output is plain-text guidance and diff-like suggestions only. P1
  adds a small LibCST-backed codemod set. `causatrace fix` is preview-only by
  default; source files are modified only with explicit `--write`.
- Verification defaults to the unmasked clean-machine profile. It requires five
  successful runs with identical stdout, stderr,
  and artifact hashes. The clean-machine profile uses UTC, C.UTF-8, a different
  hash seed, shuffled listing order, and a minimal declared environment; it
  intentionally does not seed Python or NumPy randomness.
- P1 CSV and JUnit support are explicit tooling commands. Numeric comparisons
  use opt-in absolute and relative tolerances; P0 causal classifications remain
  exact-hash based.
- Local execution does not promise network isolation. The external Docker
  verification with `--network none` is the clean-machine security boundary.
- Fixture benchmarks measure planted cases, not real-world accuracy.

## Project status

P0 and the scoped P1 tooling are implemented. P1 includes pytest/JUnit support,
tolerances, CSV comparisons, verified fixes, clean-machine profiles, HTML
reports, and the fixture benchmark command. `libcst` is a P1 runtime dependency,
not part of the P0 execution path.
