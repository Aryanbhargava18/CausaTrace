"""Exercise the listing APIs that the P0 shim promises to control."""

from __future__ import annotations

import glob
import os
from pathlib import Path


root = Path(__file__).with_name("listing_entries")
values = {
    "listdir": tuple(os.listdir(root)),
    "scandir": tuple(entry.name for entry in os.scandir(root)),
    "glob": tuple(Path(item).name for item in glob.glob(str(root / "*.txt"))),
    "iglob": tuple(Path(item).name for item in glob.iglob(str(root / "*.txt"))),
    "iterdir": tuple(item.name for item in root.iterdir()),
    "path_glob": tuple(item.name for item in root.glob("*.txt")),
    "path_rglob": tuple(item.name for item in root.rglob("*.txt")),
}

for name, entries in values.items():
    print(f"{name}=" + ",".join(entries))
