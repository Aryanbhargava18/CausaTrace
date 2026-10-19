"""Fixture that preserves the order returned by a directory listing."""

import os
from pathlib import Path


entries = Path(__file__).with_name("listing_entries")
print("|".join(os.listdir(entries)))
