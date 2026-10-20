.PHONY: test demo judge

test:
	uv run --offline pytest

demo:
	uv run --offline causatrace demo --project-root .

judge:
	uv lock --check
	uv run --offline python -m compileall -q src/causatrace tests
	$(MAKE) test
	$(MAKE) demo
	uv run --offline causatrace benchmark --project-root .
