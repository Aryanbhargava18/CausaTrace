"""Fixture with a deterministic timestamp rendered in the local timezone."""

from datetime import datetime


# This fixed instant is near midnight UTC, so supported zones can move it to a
# different local calendar date. Keeping the timestamp fixed makes the probe
# deterministic at every runtime.
print(datetime.fromtimestamp(1_704_151_800).date().isoformat())
