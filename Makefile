.PHONY: test demo example judge

test:
	uv run --offline pytest

demo:
	uv run --offline causatrace demo --project-root .

example:
	uv run --offline causatrace analyze examples/model_accuracy.py --project-root . \
		--metric-regex 'accuracy=(?P<accuracy>[0-9.]+)'

judge:
	uv lock --check
	uv run --offline python -m compileall -q src/causatrace tests examples
	$(MAKE) test
	$(MAKE) demo
	$(MAKE) example
	uv run --offline causatrace benchmark --project-root .
