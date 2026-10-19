"""Stable control fixture: no process, clock, filesystem, or environment input."""

import json


def main() -> None:
    result = {
        "items": ["alpha", "beta", "gamma"],
        "score": 42,
        "version": 1,
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
