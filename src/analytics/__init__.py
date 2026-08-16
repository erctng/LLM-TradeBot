"""Performance analytics: single source of truth for KPI computation."""
from src.analytics.performance import (
    KPIResult,
    compute_kpis,
    load_closed_trades,
    CRYPTO_PERIODS_PER_YEAR,
    SP500_REFERENCE_ANNUAL_PCT,
)

__all__ = [
    'KPIResult',
    'compute_kpis',
    'load_closed_trades',
    'CRYPTO_PERIODS_PER_YEAR',
    'SP500_REFERENCE_ANNUAL_PCT',
]
