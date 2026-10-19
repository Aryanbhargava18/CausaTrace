"""External Docker verification for timezone data in the final image."""

from __future__ import annotations

import os
from datetime import datetime, timezone
import time


FIXED_TIMESTAMP = 1_704_151_800  # 2024-01-01 23:30:00 UTC


def local_date(zone: str) -> str:
    os.environ["TZ"] = zone
    time.tzset()
    return datetime.fromtimestamp(FIXED_TIMESTAMP, tz=timezone.utc).astimezone().date().isoformat()


def main() -> None:
    utc = local_date("UTC")
    kolkata = local_date("Asia/Kolkata")
    assert utc == "2024-01-01", utc
    assert kolkata == "2024-01-02", kolkata
    print("timezone data available")


if __name__ == "__main__":
    main()
