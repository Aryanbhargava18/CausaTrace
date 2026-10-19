# CausaTrace

CausaTrace is an offline Python CLI for investigating why a script or experiment
changes its output across runs or machines. It establishes a pinned baseline,
changes one tested factor at a time, compares outputs, records supported source
locations, and reports only evidence-backed findings.

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

## Planned usage

The P0 command will accept a Python script and emit a readable terminal report
or machine-readable JSON. The report will distinguish a stable baseline,
baseline instability, factor-supported changes, suspected interactions, and
inconclusive experiments.

```bash
causatrace analyze path/to/script.py
causatrace analyze path/to/script.py --format json
```

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
- P0 repair output is plain-text guidance and diff-like suggestions only.
  `libcst` codemods and verified writes are P1 work. Source files are never
  modified unless an explicit write workflow is implemented and requested.
- Local execution does not promise network isolation. The external Docker
  verification with `--network none` is the clean-machine security boundary.
- Fixture benchmarks measure planted cases, not real-world accuracy.

## Project status

The implementation is being delivered in small green milestones. P0 is the
release target; pytest/JUnit support, tolerances, CSV comparisons, verified
fixes, clean-machine profiles, HTML reports, and benchmark tooling are P1.
