"""Intentionally baseline-flaky fixture: each process prints a fresh timestamp."""

import time


print(time.time_ns())
