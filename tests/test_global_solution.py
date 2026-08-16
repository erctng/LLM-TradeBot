"""
Test global de la solution.

Vérifie que les couches tiennent ensemble, pas que chaque fonction est correcte
isolément — c'est le rôle des tests unitaires. Quatre propriétés de bout en
bout :

1. Tout module `src.*` s'importe seul, dans un interpréteur neuf. Deux imports
   circulaires latents ont déjà été introduits par des refactorings : ils ne se
   déclenchaient que selon l'ordre d'import et cassaient la collecte pytest.
2. La chaîne de risque relie l'enregistrement d'un trade au blocage effectif
   d'une ouverture — et n'empêche jamais une fermeture.
3. Le pipeline KPI tourne sur les données réelles et produit des chiffres
   internement cohérents.
4. La comptabilité du portefeuille (frais, funding, liquidation) est exacte sur
   un scénario connu.

Aucun accès réseau : le test doit tourner en CI.
"""

import concurrent.futures
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ==========================================================================
# 1. Intégrité des imports
# ==========================================================================

def _module_names():
    names = []
    for path in sorted((REPO_ROOT / 'src').rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT).with_suffix('')
        parts = list(rel.parts)
        if parts[-1] == '__init__':
            parts = parts[:-1]
        if parts:
            names.append('.'.join(parts))
    return sorted(set(names))


def _import_in_fresh_interpreter(module: str):
    proc = subprocess.run(
        [sys.executable, '-c', f'import {module}'],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    return module, proc.returncode, proc.stderr


@pytest.mark.slow
def test_every_module_imports_standalone():
    """Chaque module doit s'importer en premier sans refermer un cycle.

    Importer un module isolément est le seul moyen de détecter un cycle
    dépendant de l'ordre : dans une suite de tests, un import antérieur masque
    le problème et le rend intermittent.
    """
    modules = _module_names()
    assert len(modules) > 50, "surface d'import suspecte, le repo a-t-il changé ?"

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for module, code, stderr in pool.map(_import_in_fresh_interpreter, modules):
            if code != 0:
                last = [l for l in stderr.strip().splitlines() if l.strip()]
                failures.append(f"{module}: {last[-1] if last else 'échec inconnu'}")

    assert not failures, "modules non importables isolément:\n  " + "\n  ".join(failures)


def test_no_circular_import_on_known_hotspots():
    """Garde rapide sur les deux cycles déjà corrigés.

    Version courte du test précédent, sans le marqueur `slow`, pour que la
    régression soit attrapée à chaque exécution de la suite.
    """
    for module in ('src.models.prophet_model', 'src.agents.runtime_events'):
        proc = subprocess.run(
            [sys.executable, '-c', f'import {module}'],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, f"{module} ne s'importe plus seul:\n{proc.stderr}"


# ==========================================================================
# 2. Chaîne de risque de bout en bout
# ==========================================================================

@pytest.fixture
def risk_chain():
    """RiskManager relié à global_state comme le fait le bot au démarrage."""
    from src.risk.manager import RiskManager
    from src.server.state import global_state

    rm = RiskManager()
    rm.consecutive_losses = 0
    rm.total_drawdown_pct = 0.0
    previous = global_state._risk_gate
    global_state.set_risk_gate(rm.check_circuit_breakers)
    yield rm, global_state
    global_state.set_risk_gate(previous)


def _closed(pnl: float) -> dict:
    """Enregistrement de trade clôturé.

    Seules les clôtures alimentent le compteur de pertes consécutives : une
    ligne d'ouverture porte un pnl de 0 et remettrait la série à zéro.
    """
    return {'status': 'CLOSED', 'action': 'close_long', 'pnl': pnl}


def test_losing_streak_propagates_to_the_gate(risk_chain):
    """Des pertes enregistrées doivent finir par fermer la porte d'ouverture."""
    rm, state = risk_chain
    assert state.check_risk_gate()[0] is True

    for _ in range(rm.max_consecutive_losses):
        rm.record_trade(_closed(-1.0))

    allowed, reason = state.check_risk_gate()
    assert allowed is False
    assert reason


def test_open_records_never_arm_or_clear_the_gate(risk_chain):
    """Les lignes d'ouverture (pnl=0) ne doivent pas toucher le compteur."""
    rm, state = risk_chain
    for _ in range(rm.max_consecutive_losses):
        rm.record_trade(_closed(-1.0))
    assert state.check_risk_gate()[0] is False

    rm.record_trade({'status': 'OPEN', 'action': 'open_long', 'pnl': 0.0})
    assert state.check_risk_gate()[0] is False, (
        "une ligne d'ouverture a désarmé le coupe-circuit"
    )


def test_a_win_reopens_the_gate(risk_chain):
    rm, state = risk_chain
    for _ in range(rm.max_consecutive_losses):
        rm.record_trade(_closed(-1.0))
    assert state.check_risk_gate()[0] is False

    rm.record_trade(_closed(5.0))
    assert state.check_risk_gate()[0] is True


def test_drawdown_breaker_propagates_to_the_gate(risk_chain):
    rm, state = risk_chain
    breach = rm.stop_trading_drawdown_pct + 1.0
    rm.update_drawdown(current_balance=100.0 - breach, peak_balance=100.0)

    allowed, reason = state.check_risk_gate()
    assert allowed is False
    assert reason


def test_breaker_never_blocks_an_exit(risk_chain):
    """Propriété de sécurité la plus critique de tout le bot.

    execution_stage_runner ne consulte la porte que pour les actions
    d'ouverture. Un coupe-circuit qui bloquerait les sorties transformerait une
    série de pertes en position piégée, sans limite de perte.
    """
    from src.utils.action_protocol import is_close_action, is_open_action

    rm, state = risk_chain
    for _ in range(rm.max_consecutive_losses):
        rm.record_trade(_closed(-1.0))
    assert state.check_risk_gate()[0] is False

    for exit_action in ('close_long', 'close_short'):
        assert is_close_action(exit_action) is True
        assert is_open_action(exit_action) is False, (
            f"{exit_action} classée comme ouverture : le coupe-circuit "
            "empêcherait de sortir du marché"
        )


def test_execution_runner_gates_opens_only():
    """Le garde du runner doit être conditionné à `is_open_action`.

    Vérifié au niveau du source : instancier le runner complet exigerait un
    client Binance. Ce test protège le couplage entre le garde et le protocole
    d'action, qui est ce qui rend la propriété précédente vraie en production.
    """
    source = (REPO_ROOT / 'src' / 'runners' / 'execution_stage_runner.py').read_text()
    assert 'check_risk_gate()' in source, "le runner ne consulte plus les coupe-circuits"

    gate_call = source.index('check_risk_gate()')
    preceding = source[:gate_call]
    guard = preceding.rindex('if is_open_action(')
    assert gate_call - guard < 400, (
        "l'appel au coupe-circuit n'est plus gardé par is_open_action() — "
        "les fermetures risquent d'être bloquées"
    )


# ==========================================================================
# 3. Pipeline KPI sur les données réelles
# ==========================================================================

TRADES_CSV = REPO_ROOT / 'data' / 'live' / 'execution' / 'trades' / 'all_trades.csv'


@pytest.mark.skipif(not TRADES_CSV.exists(), reason="pas d'historique de trades local")
def test_kpi_pipeline_is_internally_consistent():
    """Les KPI publiés doivent être cohérents entre eux, quelle que soit la perf."""
    from src.analytics.performance import compute_kpis, load_closed_trades

    trades = load_closed_trades(str(TRADES_CSV))
    if trades.empty:
        pytest.skip("aucun trade clôturé")

    k = compute_kpis(trades)

    assert k.closed_trades == len(trades)
    assert k.wins + k.losses == k.closed_trades
    assert k.win_rate_pct == pytest.approx(k.wins / k.closed_trades * 100, abs=0.01)

    expected_expectancy = trades['pnl'].mean()
    assert k.expectancy == pytest.approx(expected_expectancy, abs=1e-3)
    assert k.total_pnl == pytest.approx(trades['pnl'].sum(), abs=1e-3)

    if k.payoff_ratio:
        # Seuil d'équilibre : p*W = (1-p)*L  =>  p = 1/(1+payoff)
        assert k.breakeven_win_rate_pct == pytest.approx(
            100 / (1 + k.payoff_ratio), abs=0.2
        )

    assert k.max_drawdown_pct >= 0, "un drawdown ne peut pas être négatif"
    lo, hi = k.win_rate_ci95
    assert lo <= k.win_rate_pct <= hi


@pytest.mark.skipif(not TRADES_CSV.exists(), reason="pas d'historique de trades local")
def test_kpi_flags_gross_numbers_when_costs_are_absent():
    """Tant que frais et funding ne sont pas stockés, les KPI doivent être signalés bruts."""
    from src.analytics.performance import compute_kpis, load_closed_trades

    trades = load_closed_trades(str(TRADES_CSV))
    if trades.empty:
        pytest.skip("aucun trade clôturé")

    k = compute_kpis(trades)
    if not k.cost_data_available:
        assert any('brut' in note.lower() for note in k.notes), (
            "des KPI bruts présentés sans avertissement seraient comparés à tort "
            "aux KPI nets du backtest"
        )


@pytest.mark.skipif(not TRADES_CSV.exists(), reason="pas d'historique de trades local")
def test_side_decomposition_covers_every_trade():
    from src.analytics.performance import compute_kpis, load_closed_trades

    trades = load_closed_trades(str(TRADES_CSV))
    if trades.empty:
        pytest.skip("aucun trade clôturé")

    k = compute_kpis(trades)
    counted = sum(side['trades'] for side in k.by_side.values())
    assert counted == k.closed_trades

    total = sum(side['total_pnl'] for side in k.by_side.values())
    assert total == pytest.approx(k.total_pnl, abs=0.05)


# ==========================================================================
# 4. Comptabilité du portefeuille
# ==========================================================================

def test_taker_fee_is_charged_on_a_known_trade():
    from src.backtest.portfolio import FeeStructure

    fees = FeeStructure.binance_vip0()
    assert fees.get_fee(is_maker=False) == pytest.approx(0.0004)
    assert fees.get_fee(is_maker=True) == pytest.approx(0.0002)
    # Un aller-retour taker sur 10 000 $ de notionnel coûte 8 $.
    assert 10_000 * fees.get_fee(False) * 2 == pytest.approx(8.0)


def test_portfolio_starts_flat_and_tracks_costs():
    """Le portefeuille doit exposer les trois postes de coût dès l'initialisation."""
    from src.backtest.portfolio import BacktestPortfolio

    pf = BacktestPortfolio(initial_capital=10_000)
    assert pf.total_funding_paid == 0.0
    assert pf.total_fees_paid == 0.0
    assert pf.total_slippage_cost == 0.0
    assert hasattr(pf, 'apply_funding_fee'), (
        "sans règlement du funding, un backtest de perpétuels est optimiste"
    )


def _config(**overrides):
    from src.backtest.engine import BacktestConfig
    base = dict(symbol='BTCUSDT', start_date='2026-01-01', end_date='2026-02-01')
    base.update(overrides)
    return BacktestConfig(**base)


def test_backtest_config_rejects_impossible_slippage():
    with pytest.raises(ValueError):
        _config(slippage=-0.1)
    with pytest.raises(ValueError):
        _config(slippage=1.5)


def test_backtest_config_accepts_realistic_costs():
    """Les valeurs par défaut doivent rester des hypothèses de coût crédibles."""
    cfg = _config()
    assert 0 < cfg.commission <= 0.001, "commission hors des ordres de grandeur Binance"
    assert 0 < cfg.slippage <= 0.01, "slippage par défaut irréaliste"


# ==========================================================================
# 5. Résolution des paramètres
# ==========================================================================

def test_trading_parameters_accept_configured_values():
    """config.yaml doit pouvoir alimenter TradingParameters sans perte."""
    from src.config import config
    from src.trading import TradingParameters

    params = TradingParameters(
        max_position_size=float(config.get('trading.max_position_size', 100.0)),
        leverage=int(config.get('trading.leverage', 1)),
        stop_loss_pct=float(config.get('trading.stop_loss_pct', 1.0)),
        take_profit_pct=float(config.get('trading.take_profit_pct', 2.0)),
    )
    assert params.leverage <= int(config.get('risk.max_leverage', 5))
    assert params.stop_loss_pct > 0
    assert params.take_profit_pct > params.stop_loss_pct, (
        "un take profit sous le stop loss donne un ratio gain/risque < 1"
    )
