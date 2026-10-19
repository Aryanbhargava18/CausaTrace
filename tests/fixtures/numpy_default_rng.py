"""Intentionally unstable fixture using an unseeded NumPy Generator."""

import numpy as np


print(f"{np.random.default_rng().random():.17g}")
