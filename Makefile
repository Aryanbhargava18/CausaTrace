.PHONY: test demo judge

test:
	uv run --offline pytest

demo:
	uv run --offline causatrace demo --project-root .

judge:
	$(MAKE) test
	$(MAKE) demo
	uv run --offline causatrace benchmark --project-root .
