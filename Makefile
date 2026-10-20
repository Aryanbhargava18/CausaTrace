.PHONY: test demo

test:
	uv run --offline pytest

demo:
	uv run --offline causatrace demo --project-root .
