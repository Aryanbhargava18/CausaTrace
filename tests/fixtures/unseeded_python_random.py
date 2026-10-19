"""Intentionally unstable fixture using Python's unseeded module RNG."""

import random


print(f"{random.random():.17g}")
