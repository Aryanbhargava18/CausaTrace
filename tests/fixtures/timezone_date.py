"""Fixture with a deterministic timestamp rendered in the local timezone."""

from datetime import datetime


# The epoch is on different local dates in UTC and New York.  Keeping the
# timestamp fixed makes the timezone treatment deterministic at every runtime.
print(datetime.fromtimestamp(0).date().isoformat())
