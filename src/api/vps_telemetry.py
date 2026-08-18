"""API de télémétrie VPS.

Expose les KPI de performance calculés par `src.analytics.performance`, qui est
la source unique de vérité pour la maths. Cette API ne fait plus aucun calcul
elle-même : les formules précédentes étaient fausses (Sharpe par trade non
annualisé présenté comme un Sharpe, drawdown en dollars présenté en pourcent)
et divergeaient de celles du backtest.

Elle lit également la bonne source. Les trades réels sont écrits en CSV par
DataSaver ; la table `trades` de trading.db n'a aucun écrivain en production.
"""
import os
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from src.analytics.performance import (
    DEFAULT_TRADES_CSV,
    SP500_REFERENCE_ANNUAL_PCT,
    compute_kpis,
    load_closed_trades,
)

app = FastAPI(title="LLM-TradeBot VPS Telemetry")

API_TOKEN = os.getenv("VPS_API_TOKEN", "super-secret-token-123")
TRADES_PATH = os.getenv("TRADES_CSV_PATH", DEFAULT_TRADES_CSV)
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "1000"))
BENCHMARK_ANNUAL_PCT = float(
    os.getenv("BENCHMARK_ANNUAL_PCT", str(SP500_REFERENCE_ANNUAL_PCT))
)


def verify_token(x_token: str = Header(...)):
    if x_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized Token")
    return x_token


class PerformanceMetrics(BaseModel):
    # Échantillon
    closed_trades: int
    period_start: str
    period_end: str
    period_days: int
    is_significant: bool

    # Résultat
    total_pnl: float
    total_return_pct: float
    annualized_return_pct: Optional[float]

    # Trades
    win_rate_pct: float
    win_rate_ci95: List[float]
    wins: int
    losses: int
    avg_win: float
    avg_loss: float
    payoff_ratio: float
    profit_factor: Optional[float]
    expectancy: float

    # Risque
    max_drawdown_pct: float
    sharpe_ratio: Optional[float]
    sortino_ratio: Optional[float]
    calmar_ratio: Optional[float]
    max_consecutive_losses: int

    # Diagnostic
    breakeven_win_rate_pct: float
    required_payoff_ratio: Optional[float]
    edge_verdict: str

    # Benchmark
    benchmark_annual_pct: float
    excess_return_annual_pct: Optional[float]
    beats_benchmark: Optional[bool]

    # Qualité de la donnée
    cost_data_available: bool
    notes: List[str]
    by_side: Dict[str, Any]


def _load_metrics() -> Dict[str, Any]:
    trades = load_closed_trades(TRADES_PATH)
    result = compute_kpis(
        trades,
        initial_capital=INITIAL_CAPITAL,
        benchmark_annual_pct=BENCHMARK_ANNUAL_PCT,
    )
    return result.to_dict()


@app.get("/metrics", response_model=PerformanceMetrics, dependencies=[Depends(verify_token)])
async def get_metrics():
    """KPI de performance, nets de ce que le stockage contient.

    `notes` signale explicitement les limites de la mesure (échantillon trop
    petit, absence de données de coûts) plutôt que de publier un chiffre
    faussement précis.
    """
    if not os.path.exists(TRADES_PATH):
        raise HTTPException(
            status_code=404,
            detail=f"Trade store not found: {TRADES_PATH}",
        )

    try:
        data = _load_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return PerformanceMetrics(**{
        k: v for k, v in data.items()
        if k in PerformanceMetrics.model_fields
    })


@app.get("/health")
async def health():
    """Sonde de vie sans authentification, pour le monitoring externe."""
    return {
        "status": "ok",
        "trade_store": TRADES_PATH,
        "trade_store_present": os.path.exists(TRADES_PATH),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8085)
