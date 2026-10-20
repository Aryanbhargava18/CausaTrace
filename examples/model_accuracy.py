"""Offline miniature classifier whose accuracy depends on hidden ordering."""

from __future__ import annotations

import csv
import math
import os
from pathlib import Path


DATA_DIR = Path(__file__).with_name("model_data")


def load_records() -> list[tuple[str, float, float]]:
    """Load records in filesystem order, deliberately without sorting them."""

    records: list[tuple[str, float, float]] = []
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".csv"):
            continue
        with (DATA_DIR / filename).open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        records.append((row["label"], float(row["x"]), float(row["y"])))
    return records


def main() -> None:
    records = load_records()
    split = len(records) // 2
    training, testing = records[:split], records[split:]

    points_by_label: dict[str, list[tuple[float, float]]] = {}
    for label, x, y in training:
        points_by_label.setdefault(label, []).append((x, y))
    centroids = {
        label: (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        for label, points in points_by_label.items()
    }

    correct = 0
    for expected, x, y in testing:
        distances = {
            label: (x - centroid[0]) ** 2 + (y - centroid[1]) ** 2
            for label, centroid in centroids.items()
        }
        nearest = min(distances.values())
        tied_labels = {
            label for label, distance in distances.items() if math.isclose(distance, nearest)
        }
        # A tied prediction uses set iteration order: a hidden hash seed becomes
        # a model decision, not just a presentation difference.
        prediction = next(iter(tied_labels))
        correct += prediction == expected

    accuracy = correct / len(testing)
    print("experiment=order-sensitive-centroid-classifier")
    print(f"train_records={len(training)} test_records={len(testing)}")
    print(f"accuracy={accuracy:.2f}")


if __name__ == "__main__":
    main()
