"""Fixture whose output is controlled by one declared environment variable."""

import os


print(os.environ.get("CAUSATRACE_FIXTURE_VALUE", "<unset>"))
