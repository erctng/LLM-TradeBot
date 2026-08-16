"""Tests du module KPI — verrouillent les corrections de formules.

Couvrent les trois défauts de l'implémentation précédente : Sharpe non
annualisé, drawdown en dollars étiqueté en pourcent, et filtre de clôture sur
la mauvaise colonne.
"""
import numpy as np
import pandas as pd
import pytest

from src.analytics.performance import (
    CRYPTO_PERIODS_PER_YEAR,
    compute_kpis,
    load_closed_trades,
)


def _trades(pnls, start='2026-01-01', side='OPEN_LONG'):
    ts = pd.date_range(start, periods=len(pnls), freq='D')
    return pd.DataFrame({
        'timestamp': ts,
        'pnl': pnls,
        'side': ['SHORT' if 'SHORT' in side else 'LONG'] * len(pnls),
        'action': [side] * len(pnls),
    })


def test_closed_trades_identified_by_status_not_action(tmp_path):
    """Une ligne clôturée garde son action d'ouverture : filtrer sur `action`
    ne remonte rien. C'est le bug qui vidait la télémétrie."""
    csv = tmp_path / 'all_trades.csv'
    pd.DataFrame({
        'record_time': ['2026-01-01 10:00:00', '2026-01-02 10:00:00', '2026-01-03 10:00:00'],
        'action': ['OPEN_LONG', 'OPEN_LONG', 'OPEN_SHORT'],
        'symbol': ['BTCUSDT'] * 3,
        'pnl': [0.0, 12.5, -4.0],
        'status': ['SIMULATED', 'CLOSED', 'CLOSED'],
    }).to_csv(csv, index=False)

    loaded = load_closed_trades(str(csv))
    assert len(loaded) == 2, "must find CLOSED rows despite action still saying OPEN_*"
    assert set(loaded['side']) == {'LONG', 'SHORT'}


def test_sharpe_is_annualised_not_per_trade():
    """Le Sharpe doit porter le facteur sqrt(365), pas être un mean/std brut."""
    rng = np.random.default_rng(42)
    pnls = list(rng.normal(1.0, 5.0, 200))
    k = compute_kpis(_trades(pnls), initial_capital=10_000.0)

    equity = 10_000 + pd.Series(pnls).cumsum()
    returns = equity.pct_change().dropna()
    raw = returns.mean() / returns.std(ddof=1)

    assert k.sharpe_ratio is not None
    # Le Sharpe annualisé doit être d'un ordre de grandeur sqrt(365) au-dessus
    assert abs(k.sharpe_ratio) > abs(raw) * 10
    assert k.sharpe_ratio == pytest.approx(raw * np.sqrt(CRYPTO_PERIODS_PER_YEAR), rel=0.05)


def test_drawdown_is_a_percentage_of_equity():
    """Perte de 200 sur un capital de 1000 => 20%, pas 200."""
    k = compute_kpis(_trades([100.0, -300.0]), initial_capital=1000.0)
    assert k.max_drawdown_pct == pytest.approx(27.27, abs=0.5)
    assert k.max_drawdown_abs == pytest.approx(300.0, abs=1.0)
    assert k.max_drawdown_pct < 100, "a percentage cannot be a dollar amount"


def test_expectancy_and_breakeven_diagnostics():
    """40% de réussite avec payoff 1.0 => seuil de rentabilité à 50%."""
    pnls = [10.0] * 4 + [-10.0] * 6
    k = compute_kpis(_trades(pnls), initial_capital=1000.0)

    assert k.win_rate_pct == pytest.approx(40.0)
    assert k.payoff_ratio == pytest.approx(1.0)
    assert k.expectancy == pytest.approx(-2.0)
    assert k.breakeven_win_rate_pct == pytest.approx(50.0)
    assert k.required_payoff_ratio == pytest.approx(1.5)
    assert 'négative' in k.edge_verdict


def test_no_annualisation_under_90_days():
    """Annualiser un historique court produit un chiffre trompeur."""
    k = compute_kpis(_trades([1.0] * 30), initial_capital=1000.0)
    assert k.annualized_return_pct is None
    assert k.beats_benchmark is None
    assert any('annualis' in n for n in k.notes)


def test_annualisation_and_benchmark_above_90_days():
    k = compute_kpis(_trades([1.0] * 200), initial_capital=1000.0, benchmark_annual_pct=10.5)
    assert k.annualized_return_pct is not None
    assert k.excess_return_annual_pct == pytest.approx(
        k.annualized_return_pct - 10.5, abs=0.01
    )
    assert k.beats_benchmark is (k.excess_return_annual_pct > 0)


def test_small_sample_flagged_as_insignificant():
    k = compute_kpis(_trades([1.0, -1.0, 2.0]), initial_capital=1000.0)
    assert k.is_significant is False
    assert any('significatif' in n for n in k.notes)
    # L'intervalle de confiance doit être large sur 3 trades
    lo, hi = k.win_rate_ci95
    assert hi - lo > 40


def test_missing_cost_columns_are_reported():
    k = compute_kpis(_trades([1.0] * 40), initial_capital=1000.0)
    assert k.cost_data_available is False
    assert any('frais' in n for n in k.notes)


def test_max_consecutive_losses():
    k = compute_kpis(_trades([1.0, -1, -1, -1, -1, 2.0, -1, -1]), initial_capital=1000.0)
    assert k.max_consecutive_losses == 4


def test_empty_input_is_safe():
    k = compute_kpis(pd.DataFrame(), initial_capital=1000.0)
    assert k.closed_trades == 0
    assert k.sharpe_ratio is None
    assert 'Aucun trade' in k.edge_verdict
