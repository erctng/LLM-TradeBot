"""KPI de performance — source unique de vérité.

Ce module remplace les formules dispersées (et fausses) de vps_telemetry.py.
Il est volontairement sans dépendance autre que pandas/numpy afin de pouvoir
être appelé aussi bien par l'API de télémétrie que par un script d'analyse.

Trois corrections structurelles par rapport à l'implémentation précédente :

1. Le Sharpe est annualisé sur une courbe d'equity **journalière**, avec la
   périodicité explicite (365 jours : le crypto cote en continu, jamais 252).
   L'ancienne version renvoyait `mean/std` par trade, c'est-à-dire une
   expectancy normalisée, non comparable au Sharpe du backtest.

2. Le drawdown est calculé sur la courbe d'equity et exprimé en pourcentage.
   L'ancienne version renvoyait un montant absolu sous un champ nommé
   `max_drawdown_pct`.

3. Les trades clôturés sont identifiés par `status`, pas par `action`. Dans le
   stockage réel, une ligne clôturée conserve son action d'ouverture
   (`OPEN_LONG`) et bascule `status` en `CLOSED` — le filtre `action == 'close'`
   ne remontait donc rien.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Le crypto cote 24/7 : l'annualisation se fait sur 365 jours, pas 252.
CRYPTO_PERIODS_PER_YEAR = 365

# Rendement total annualisé de référence du S&P 500 (nominal, dividendes
# réinvestis, moyenne long terme). Constante documentée et non une donnée de
# marché live : aucune API payante n'est requise. Ajustable par l'appelant.
SP500_REFERENCE_ANNUAL_PCT = 10.5

# En dessous de ce nombre de trades clôturés, aucun ratio n'est significatif.
MIN_SIGNIFICANT_TRADES = 30

DEFAULT_TRADES_CSV = os.path.join(
    'data', 'live', 'execution', 'trades', 'all_trades.csv'
)


@dataclass
class KPIResult:
    """Jeu complet d'indicateurs. Tous les montants sont nets de ce que le
    stockage contient — voir `cost_data_available`."""

    # Échantillon
    closed_trades: int
    total_records: int
    period_start: str
    period_end: str
    period_days: int
    is_significant: bool

    # Résultat
    total_pnl: float
    initial_capital: float
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
    profit_factor: float
    expectancy: float
    largest_win: float
    largest_loss: float

    # Risque
    max_drawdown_pct: float
    max_drawdown_abs: float
    sharpe_ratio: Optional[float]
    sortino_ratio: Optional[float]
    calmar_ratio: Optional[float]
    volatility_annual_pct: Optional[float]
    max_consecutive_losses: int

    # Diagnostic d'edge
    breakeven_win_rate_pct: float
    required_payoff_ratio: float
    edge_verdict: str

    # Benchmark
    benchmark_annual_pct: float
    excess_return_annual_pct: Optional[float]
    beats_benchmark: Optional[bool]

    # Qualité de la donnée
    cost_data_available: bool
    cost_drag_pct: Optional[float]
    # Part des trades clôturés portant réellement une donnée de coût. Les
    # colonnes peuvent exister tout en étant vides sur l'historique antérieur à
    # leur ajout : une couverture partielle rend les KPI « nets » trompeurs.
    cost_coverage_pct: float = 0.0
    total_fees: Optional[float] = None
    total_funding: Optional[float] = None
    net_pnl: Optional[float] = None
    net_expectancy: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    # Décomposition
    by_side: Dict[str, Dict] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


def _normalise_side(action: str) -> str:
    """Le côté du trade se lit dans l'action d'ouverture conservée sur la ligne."""
    a = str(action).upper()
    if 'SHORT' in a or 'SELL' in a:
        return 'SHORT'
    if 'LONG' in a or 'BUY' in a:
        return 'LONG'
    return 'UNKNOWN'


def load_closed_trades(path: str = DEFAULT_TRADES_CSV) -> pd.DataFrame:
    """Charge les trades clôturés depuis le stockage réel.

    Un trade est clôturé quand `status` contient CLOSED et que le PnL est
    renseigné. Filtrer sur `action` ne fonctionne pas : l'action reste celle de
    l'ouverture après clôture.
    """
    if not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_csv(path)
    if df.empty:
        return df

    if 'status' not in df.columns or 'pnl' not in df.columns:
        return pd.DataFrame()

    df['pnl'] = pd.to_numeric(df['pnl'], errors='coerce')
    closed = df[
        df['status'].astype(str).str.contains('CLOSED', case=False, na=False)
        & df['pnl'].notna()
        & (df['pnl'] != 0)
    ].copy()

    if closed.empty:
        return closed

    time_col = 'record_time' if 'record_time' in closed.columns else closed.columns[0]
    closed['timestamp'] = pd.to_datetime(closed[time_col], errors='coerce')
    closed = closed.dropna(subset=['timestamp']).sort_values('timestamp')
    closed['side'] = closed.get('action', '').apply(_normalise_side)

    return closed.reset_index(drop=True)


def _daily_equity_curve(trades: pd.DataFrame, initial_capital: float) -> pd.Series:
    """Courbe d'equity journalière, calendaire (le crypto ne ferme pas)."""
    daily_pnl = trades.set_index('timestamp')['pnl'].resample('D').sum()
    if daily_pnl.empty:
        return pd.Series(dtype=float)
    return initial_capital + daily_pnl.cumsum()


def _max_drawdown(equity: pd.Series) -> tuple:
    """Drawdown maximal sur la courbe d'equity : (pct, montant absolu)."""
    if equity.empty:
        return 0.0, 0.0
    peak = equity.cummax()
    dd_abs = peak - equity
    dd_pct = (dd_abs / peak.replace(0, np.nan)) * 100
    return float(dd_pct.max() or 0.0), float(dd_abs.max() or 0.0)


def _max_consecutive_losses(pnl: pd.Series) -> int:
    streak = best = 0
    for value in pnl:
        if value < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _annualise_return(total_return_pct: float, days: int) -> Optional[float]:
    """N'annualise pas en dessous de 90 jours : le chiffre serait trompeur."""
    if days < 90 or days <= 0:
        return None
    growth = 1 + (total_return_pct / 100)
    if growth <= 0:
        return -100.0
    return (growth ** (CRYPTO_PERIODS_PER_YEAR / days) - 1) * 100


def compute_kpis(
    trades: pd.DataFrame,
    initial_capital: float = 1000.0,
    benchmark_annual_pct: float = SP500_REFERENCE_ANNUAL_PCT,
    total_records: Optional[int] = None,
) -> KPIResult:
    """Calcule le jeu complet d'indicateurs sur des trades clôturés."""
    notes: List[str] = []
    n = len(trades)

    if n == 0:
        return KPIResult(
            closed_trades=0, total_records=total_records or 0,
            period_start='', period_end='', period_days=0, is_significant=False,
            total_pnl=0.0, initial_capital=initial_capital,
            total_return_pct=0.0, annualized_return_pct=None,
            win_rate_pct=0.0, win_rate_ci95=[0.0, 0.0], wins=0, losses=0,
            avg_win=0.0, avg_loss=0.0, payoff_ratio=0.0, profit_factor=0.0,
            expectancy=0.0, largest_win=0.0, largest_loss=0.0,
            max_drawdown_pct=0.0, max_drawdown_abs=0.0,
            sharpe_ratio=None, sortino_ratio=None, calmar_ratio=None,
            volatility_annual_pct=None, max_consecutive_losses=0,
            breakeven_win_rate_pct=0.0, required_payoff_ratio=0.0,
            edge_verdict='Aucun trade clôturé : rien à mesurer.',
            benchmark_annual_pct=benchmark_annual_pct,
            excess_return_annual_pct=None, beats_benchmark=None,
            cost_data_available=False, cost_drag_pct=None,
            notes=['Aucun trade clôturé dans le stockage.'],
        )

    pnl = trades['pnl'].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_rate = len(wins) / n * 100
    # Intervalle de confiance de Wald : un win rate sur petit échantillon est
    # presque toujours indistinguable du hasard, et il faut que ça se voie.
    p = win_rate / 100
    margin = 1.96 * math.sqrt(max(p * (1 - p) / n, 0.0)) * 100
    ci = [round(max(0.0, win_rate - margin), 2), round(min(100.0, win_rate + margin), 2)]

    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses.mean())) if len(losses) else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    expectancy = float(pnl.mean())

    start = trades['timestamp'].min()
    end = trades['timestamp'].max()
    period_days = max(int((end - start).days), 1)

    total_pnl = float(pnl.sum())
    total_return_pct = (total_pnl / initial_capital * 100) if initial_capital else 0.0
    annualized = _annualise_return(total_return_pct, period_days)
    if annualized is None:
        notes.append(
            f"Rendement non annualisé : {period_days} jours d'historique "
            f"(seuil 90 j). Annualiser plus tôt produit un chiffre trompeur."
        )

    equity = _daily_equity_curve(trades, initial_capital)
    dd_pct, dd_abs = _max_drawdown(equity)

    sharpe = sortino = vol_annual = None
    if len(equity) >= 3:
        returns = equity.pct_change().dropna()
        if len(returns) >= 2 and returns.std(ddof=1) > 0:
            ann = math.sqrt(CRYPTO_PERIODS_PER_YEAR)
            sharpe = float(returns.mean() / returns.std(ddof=1) * ann)
            vol_annual = float(returns.std(ddof=1) * ann * 100)
            # Sortino : le dénominateur divise par N total, pas par le nombre
            # de périodes négatives.
            downside = np.minimum(returns, 0.0)
            dd_dev = float(np.sqrt((downside ** 2).mean()))
            if dd_dev > 0:
                sortino = float(returns.mean() / dd_dev * ann)

    calmar = None
    if annualized is not None and dd_pct > 0:
        calmar = annualized / dd_pct

    # Diagnostic d'edge : quel win rate faudrait-il au payoff observé, et quel
    # payoff faudrait-il au win rate observé, pour atteindre le seuil de
    # rentabilité. C'est ce couple qui dit où se situe le problème.
    breakeven_wr = (1 / (1 + payoff) * 100) if payoff > 0 else 100.0
    required_payoff = ((1 - p) / p) if p > 0 else float('inf')

    if expectancy > 0:
        verdict = (
            f"Expectancy positive ({expectancy:+.3f}/trade). "
            f"Profit factor {profit_factor:.2f}."
        )
    else:
        gap_wr = breakeven_wr - win_rate
        gap_payoff = required_payoff - payoff
        verdict = (
            f"Expectancy négative ({expectancy:+.3f}/trade). "
            f"Au payoff actuel de {payoff:.2f}, il faudrait "
            f"{breakeven_wr:.1f}% de réussite (manque {gap_wr:.1f} pts) — "
            f"ou, à {win_rate:.1f}% de réussite, un payoff de "
            f"{required_payoff:.2f} (manque {gap_payoff:.2f}). "
            f"Les perdants sont plus gros que les gagnants."
        )

    excess = None
    beats = None
    if annualized is not None:
        excess = annualized - benchmark_annual_pct
        beats = excess > 0

    # Coûts. Une colonne présente ne signifie pas une donnée présente : après
    # l'ajout des colonnes au schéma, tout l'historique antérieur les porte
    # vides. On exige donc une couverture réelle avant d'annoncer des KPI nets.
    cost_drag = None
    total_fees = total_funding = None
    net_pnl = net_expectancy = None
    cost_coverage = 0.0

    fee_series = None
    if 'fees_paid' in trades.columns:
        fee_series = pd.to_numeric(trades['fees_paid'], errors='coerce')
    funding_series = None
    if 'funding_paid' in trades.columns:
        funding_series = pd.to_numeric(trades['funding_paid'], errors='coerce')

    if fee_series is not None:
        documented = fee_series.notna() & (fee_series != 0)
        cost_coverage = float(documented.mean() * 100) if n else 0.0

    has_costs = cost_coverage > 0
    if has_costs:
        total_fees = float(fee_series.fillna(0).sum())
        total_funding = (
            float(funding_series.fillna(0).sum()) if funding_series is not None else 0.0
        )
        total_costs = total_fees + total_funding
        gross = gross_profit + gross_loss
        cost_drag = (total_costs / gross * 100) if gross > 0 else None

        net_pnl = float(total_pnl - total_costs)
        net_expectancy = net_pnl / n if n else None

        if cost_coverage < 99.0:
            notes.append(
                f"Coûts renseignés sur {cost_coverage:.0f}% des trades seulement : "
                f"le PnL net sous-estime les frais réels sur le reste de l'historique."
            )
    else:
        notes.append(
            "KPI bruts : le stockage ne contient ni frais ni funding. "
            "Non comparables aux KPI nets du backtest."
        )

    is_significant = n >= MIN_SIGNIFICANT_TRADES
    if not is_significant:
        notes.append(
            f"Échantillon de {n} trades (< {MIN_SIGNIFICANT_TRADES}) : "
            f"aucun ratio n'est statistiquement significatif."
        )

    by_side: Dict[str, Dict] = {}
    if 'side' in trades.columns:
        for side, group in trades.groupby('side'):
            g_pnl = group['pnl'].astype(float)
            g_wins = g_pnl[g_pnl > 0]
            by_side[str(side)] = {
                'trades': len(group),
                'win_rate_pct': round(len(g_wins) / len(group) * 100, 2),
                'total_pnl': round(float(g_pnl.sum()), 2),
                'expectancy': round(float(g_pnl.mean()), 4),
            }

    return KPIResult(
        closed_trades=n,
        total_records=total_records if total_records is not None else n,
        period_start=str(start.date()),
        period_end=str(end.date()),
        period_days=period_days,
        is_significant=is_significant,
        total_pnl=round(total_pnl, 4),
        initial_capital=initial_capital,
        total_return_pct=round(total_return_pct, 4),
        annualized_return_pct=round(annualized, 4) if annualized is not None else None,
        win_rate_pct=round(win_rate, 2),
        win_rate_ci95=ci,
        wins=len(wins),
        losses=len(losses),
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        payoff_ratio=round(payoff, 4),
        profit_factor=round(profit_factor, 4) if profit_factor != float('inf') else None,
        expectancy=round(expectancy, 4),
        largest_win=round(float(pnl.max()), 4),
        largest_loss=round(float(pnl.min()), 4),
        max_drawdown_pct=round(dd_pct, 4),
        max_drawdown_abs=round(dd_abs, 4),
        sharpe_ratio=round(sharpe, 4) if sharpe is not None else None,
        sortino_ratio=round(sortino, 4) if sortino is not None else None,
        calmar_ratio=round(calmar, 4) if calmar is not None else None,
        volatility_annual_pct=round(vol_annual, 4) if vol_annual is not None else None,
        max_consecutive_losses=_max_consecutive_losses(pnl),
        breakeven_win_rate_pct=round(breakeven_wr, 2),
        required_payoff_ratio=round(required_payoff, 4) if required_payoff != float('inf') else None,
        edge_verdict=verdict,
        benchmark_annual_pct=benchmark_annual_pct,
        excess_return_annual_pct=round(excess, 4) if excess is not None else None,
        beats_benchmark=beats,
        cost_data_available=has_costs,
        cost_drag_pct=round(cost_drag, 4) if cost_drag is not None else None,
        cost_coverage_pct=round(cost_coverage, 2),
        total_fees=round(total_fees, 4) if total_fees is not None else None,
        total_funding=round(total_funding, 4) if total_funding is not None else None,
        net_pnl=round(net_pnl, 4) if net_pnl is not None else None,
        net_expectancy=round(net_expectancy, 4) if net_expectancy is not None else None,
        notes=notes,
        by_side=by_side,
    )
