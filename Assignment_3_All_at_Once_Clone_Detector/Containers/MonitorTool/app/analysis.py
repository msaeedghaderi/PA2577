from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass
class FitResult:
    kind: str
    slope: float
    intercept: float
    r2: float
    n: int

def linear_fit(x: np.ndarray, y: np.ndarray):
    if len(x) < 2:
        return None
    A = np.vstack([x, np.ones_like(x)]).T
    beta, residuals, rank, s = np.linalg.lstsq(A, y, rcond=None)
    slope, intercept = beta[0], beta[1]
    y_pred = slope * x + intercept
    ss_res = float(((y - y_pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return FitResult(kind='linear', slope=float(slope), intercept=float(intercept), r2=r2, n=len(x))

def exponential_fit(x: np.ndarray, y: np.ndarray):
    mask = y > 0
    x2, y2 = x[mask], y[mask]
    if len(x2) < 2:
        return None
    logy = np.log(y2)
    A = np.vstack([x2, np.ones_like(x2)]).T
    beta, residuals, rank, s = np.linalg.lstsq(A, logy, rcond=None)
    b, ln_a = beta[0], beta[1]
    logy_pred = b * x2 + ln_a
    ss_res = float(((logy - logy_pred) ** 2).sum())
    ss_tot = float(((logy - logy.mean()) ** 2).sum())
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return FitResult(kind='exponential', slope=float(b), intercept=float(ln_a), r2=r2, n=len(x2))

def summarize_durations(durations_ms):
    if not durations_ms:
        return {'count': 0, 'mean_ms': None, 'p50_ms': None, 'p95_ms': None}
    s = pd.Series(durations_ms, dtype=float)
    return {
        'count': int(s.size),
        'mean_ms': float(s.mean()),
        'p50_ms': float(s.quantile(0.50)),
        'p95_ms': float(s.quantile(0.95)),
    }
