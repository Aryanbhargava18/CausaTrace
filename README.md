# CausaTrace

> **An evidence-first causal reproducibility debugger for Python experiments.**

CausaTrace answers the question ordinary “run it again” debugging cannot:

> **Which tested hidden factor changed my program's result, where did it enter the execution, and did the repair actually restore reproducibility?**

It does not guess from a single trace. It establishes a pinned baseline, performs
controlled interventions, compares hashes and metrics, records runtime evidence,
separates static heuristics from proof, and refuses to claim success when the
evidence is unstable or incomplete.

## Why this matters

Scientific and data workflows often change because of an implicit seed, hash
order, filesystem order, timezone, locale, or environment assumption. Existing
test runners tell you that output changed; experiment trackers record parameters
you already knew to record. CausaTrace tests likely hidden factors directly and
returns an auditable result instead of a plausible explanation.

## The gap in existing tools

CausaTrace is complementary to mature projects rather than pretending to
replace them:

| Tool category | What it does well | What CausaTrace adds |
|---|---|---|
| [pytest](https://github.com/pytest-dev/pytest) / [Hypothesis](https://github.com/HypothesisWorks/hypothesis) | Finds failing tests and counterexample inputs | Intervenes on hidden runtime factors such as hash seed, timezone, RNG, and listing order |
| [DVC](https://github.com/iterative/dvc) / [MLflow](https://github.com/mlflow/mlflow) | Tracks declared data, parameters, metrics, and runs | Searches for undeclared causes that were never logged |
| [ReproZip](https://github.com/VIDA-NYU/reprozip) | Captures dependencies and replays an execution | Explains which tested factor changed the result and suggests a source repair |
| [rr](https://github.com/rr-debugger/rr) | Records and replays low-level Linux execution | Provides a Python/scientific-computing workflow with factor experiments and reports |
| **CausaTrace** | Controlled factor intervention plus evidence | The focused loop: **intervene → compare → localize → repair → verify** |

The project does not claim universal causal inference. It claims a narrower,
testable result: changing factor **F** under a declared protocol repeatedly
changed output **O**, with the effect and limitations recorded in the report.

## 30-second evaluator demo

From a clean checkout:

```bash
make demo
```

The offline demo:

1. Runs a planted set-order experiment.
2. Changes `PYTHONHASHSEED` in controlled paired trials.
3. Reports `factor_supported` with `3/3` consistency and a source location.
4. Verifies a clean control across five runs.
5. Deliberately rejects an unrepaired `random.random()` program.

The expected random verification failure is part of the proof: verification is
not masking nondeterminism with CausaTrace's own seeds.

For one complete acceptance gate, run `make judge`. It runs the offline test
suite, the causal demo, and the planted-case benchmark; all three must pass.

## Example result

```text
hash_seed [baseline:hash_seed]: factor_supported
Evidence: stdout SHA-256
Runtime location: tests/fixtures/hash_dependent_set.py:4

clean control: PASS (1 output hash)
unrepaired random script: EXPECTED FAIL (5 output hashes)
```

## Install and run

Requirements: Python **3.12.8** and `uv`. The committed `uv.lock` pins the
development environment.

```bash
uv sync --locked --all-groups
make test
make demo
```

Analyze a script:

```bash
uv run --offline causatrace analyze path/to/script.py --project-root .
uv run --offline causatrace analyze path/to/script.py --format json > report.json
uv run --offline causatrace analyze path/to/script.py --format html --output report.html
```

Pass target arguments after `--`:

```bash
uv run --offline causatrace analyze path/to/script.py --project-root . -- input.csv
```

## Evidence workflow

Each analysis performs:

1. **Three pinned baseline runs.** If any baseline output differs, CausaTrace
   reports `baseline_unstable` and withholds causal attribution.
2. **Three paired trials per factor.** Baseline and treatment are compared by
   stdout/stderr SHA-256, artifact hashes, exit status, and exact metrics.
3. **Treatment stability validation.** A treatment cannot be labelled supported
   merely because it differs from baseline; its own repeated evidence must also
   be stable.
4. **Runtime and static localization.** Runtime locations come from intercepted
   operations and bounded call stacks. AST findings are explicitly labelled
   heuristic and are never runtime proof.
5. **Compact audit evidence.** JSON and HTML reports include profile names,
   hashes, metrics, artifact state, exit status, timeout state, and per-trial
   baseline/treatment evidence without copying raw program output or secrets.

## Supported factor probes

- `PYTHONHASHSEED` using a fresh interpreter
- Python's module-level random generator
- NumPy's legacy generator and unseeded `default_rng()` when NumPy is available
- Filesystem listing order through supported listing hooks
- `UTC`, `Asia/Kolkata`, `America/Los_Angeles`, and `Pacific/Kiritimati`
- An all-released profile for interaction detection

The default verification profile is intentionally **unmasked**: it uses UTC,
C.UTF-8, a different hash seed, shuffled listing order, and a minimal declared
environment, but does not seed Python or NumPy randomness.

## Repairs and verification

P0 suggestions are plain text. P1 adds small LibCST-backed diff-first repairs:

```bash
uv run --offline causatrace fix path/to/script.py
uv run --offline causatrace fix path/to/script.py --write
uv run --offline causatrace verify path/to/script.py --project-root .
```

`fix` is preview-only by default. `--write` is explicit; failed verification
returns nonzero and restores the original source. Verification requires five
successful runs with identical stdout, stderr, and requested artifact hashes.

Additional P1 tools:

```bash
uv run --offline causatrace pytest --project-root .
uv run --offline causatrace junit results.xml --format json
uv run --offline causatrace compare-csv baseline.csv treatment.csv --key id
uv run --offline causatrace benchmark --project-root .
```

## Reproducible Docker check

The image uses Python 3.12 slim, installs `tzdata` and `make`, and installs the
locked environment. The default container command is the offline test suite.

```bash
docker build -t causatrace .
docker run --rm --network none causatrace
docker run --rm --network none causatrace make demo
docker run --rm --network none causatrace \
  python tests/docker/timezone_probe.py
```

The container test must run without downloading or accessing the network. The
Docker build itself may download its pinned base/build inputs.

## Why it fits Research, Data & Scientific Computing

CausaTrace treats reproducibility debugging as a controlled experiment:

- intervention rather than correlation
- paired trials rather than a single rerun
- stability checks rather than “changed once” claims
- explicit uncertainty and interaction states
- provenance and hashes rather than opaque confidence scores
- source-level suggestions verified against repeated execution

It is deliberately an offline Python CLI rather than a cloud service, making
the result inspectable, reproducible, and usable in restricted research
environments.

## Scope and honest limitations

- Python scripts and the top-level process are supported. Child-process tracing
  is intentionally unsupported and reported as a limitation.
- Local analysis is not a network or filesystem security sandbox. Use Docker
  with `--network none` for the clean-machine boundary.
- The factor inventory is finite. `factor_not_observed` means “not observed under
  the tested controls,” not “impossible in all environments.”
- GPU/CUDA, notebooks, distributed jobs, additional languages, and arbitrary
  third-party RNGs are out of scope.
- Fixture benchmarks measure planted cases, not real-world accuracy.

## Project layout

```text
src/causatrace/   runner, probes, shim, attribution, reports, fixes
tests/            unit tests, integration tests, Docker probe, planted fixtures
Dockerfile        reproducible slim image with tzdata and offline test command
Makefile          make test and make demo
uv.lock           committed dependency lock
```

## License

MIT. See [LICENSE](LICENSE).
