"""Shared feature code, so training (UCI-HAR) and testing (HAR70+) use the exact same maths."""
import numpy as np

WIN = 128   # samples per window (2.56 s at 50 Hz)
HOP = 64    # step between window starts (50% overlap)

# the order of the columns that mag_features() returns
FEATURE_NAMES = ['mag_mean', 'mag_std', 'mag_min', 'mag_max', 'mag_range']


def magnitude(x, y, z):
    """Length of the acceleration vector: sqrt(x^2 + y^2 + z^2).
    It stays the same however the sensor is rotated, which is why we use it
    (the two datasets have their sensors facing different directions)."""
    x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
    return np.sqrt(x ** 2 + y ** 2 + z ** 2)


def mag_features(mag):
    """Turn magnitude windows into 5 features each.

    mag: array of shape (n_windows, WIN), or a single window of shape (WIN,)
    returns: array of shape (n_windows, 5) in the order of FEATURE_NAMES
    """
    mag = np.asarray(mag, float)
    mean = mag.mean(axis=-1)
    std = mag.std(axis=-1)          # population std (divides by n)
    lo = mag.min(axis=-1)
    hi = mag.max(axis=-1)
    return np.stack([mean, std, lo, hi, hi - lo], axis=-1)
