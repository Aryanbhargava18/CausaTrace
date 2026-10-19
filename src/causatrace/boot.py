"""Boot entry point alias for the CausaTrace top-level shim."""

from .shim import main, run_target

__all__ = ["main", "run_target"]


if __name__ == "__main__":
    raise SystemExit(main())
