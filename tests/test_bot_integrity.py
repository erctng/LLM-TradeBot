"""
Tests d'intégrité des invariants du bot.

Couvre les propriétés qui ont déjà cassé une fois et qui ne sont protégées par
aucun test : somme des poids de vote, signe du PnL des shorts, classification
des actions dont dépendent les coupe-circuits, cohérence de la configuration,
et largeur du vecteur de features attendu par le modèle ML.
"""

import os
import sys
from dataclasses import fields

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from src.agents.decision_core.signal_weight import SignalWeight
from src.utils.action_protocol import is_close_action, is_open_action, normalize_action


# --------------------------------------------------------------------------
# Poids du DecisionCore
# --------------------------------------------------------------------------

def test_signal_weights_sum_to_one():
    """Les poids doivent totaliser 1.0.

    Ils ont valu 0.95 en production : le score pondéré ne pouvait alors jamais
    atteindre son maximum nominal, ce qui décalait silencieusement tous les
    seuils de passage à l'action de 5 %.
    """
    w = SignalWeight()
    total = sum(getattr(w, f.name) for f in fields(w) if f.type in ('float', float))
    assert total == pytest.approx(1.0, abs=1e-9), f"somme des poids = {total}, attendu 1.0"


def test_signal_weights_are_non_negative():
    w = SignalWeight()
    for f in fields(w):
        value = getattr(w, f.name)
        assert value >= 0, f"poids négatif sur {f.name}: {value}"


# --------------------------------------------------------------------------
# Protocole d'action — dont dépend l'asymétrie des coupe-circuits
# --------------------------------------------------------------------------

@pytest.mark.parametrize('action', ['open_long', 'open_short', 'OPEN_LONG', 'OPEN_SHORT'])
def test_open_actions_classified_as_open(action):
    assert is_open_action(action) is True
    assert is_close_action(action) is False


@pytest.mark.parametrize('action', ['close_long', 'close_short', 'CLOSE_LONG', 'CLOSE_SHORT'])
def test_close_actions_classified_as_close(action):
    """Propriété de sécurité critique.

    execution_stage_runner ne consulte les coupe-circuits que si
    `is_open_action(action)`. Si une action de fermeture était classée comme
    ouverture, un coupe-circuit armé empêcherait de sortir du marché et
    enfermerait le capital dans une position perdante.
    """
    assert is_close_action(action) is True
    assert is_open_action(action) is False


@pytest.mark.parametrize('action', ['wait', 'hold', None, ''])
def test_neutral_actions_are_neither_open_nor_close(action):
    assert is_open_action(action) is False
    assert is_close_action(action) is False


def test_normalize_action_is_idempotent():
    for raw in ['open_long', 'OPEN_LONG', 'close_short']:
        once = normalize_action(raw)
        assert normalize_action(once) == once


# --------------------------------------------------------------------------
# PnL en pourcentage — signe des shorts et effet du levier
# --------------------------------------------------------------------------

@pytest.fixture
def logger(tmp_path, monkeypatch):
    """TradingLogger sur une base SQLite jetable."""
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('TRADING_DB_PATH', str(tmp_path / 'trading.db'))
    from src.monitoring.logger import TradingLogger
    return TradingLogger(db_path=str(tmp_path / 'trading.db'))


def _pnl_pct_after_close(logger, side, entry, exit_price, leverage):
    logger.open_trade({
        'timestamp': '2026-01-01T00:00:00',
        'symbol': 'BTCUSDT',
        'side': side,
        'entry_price': entry,
        'quantity': 1.0,
        'leverage': leverage,
    })
    logger.close_trade('BTCUSDT', exit_price=exit_price, pnl=0.0)

    from sqlalchemy import text
    with logger.engine.connect() as conn:
        return conn.execute(
            text("SELECT pnl_pct FROM trades WHERE symbol='BTCUSDT' ORDER BY id DESC LIMIT 1")
        ).scalar()


def test_short_winner_has_positive_pnl_pct(logger):
    """Un short gagnant sort à un prix inférieur à l'entrée.

    Sans prise en compte de la direction, chaque short rentable était enregistré
    avec un rendement négatif — et toutes les statistiques agrégées par côté
    étaient donc fausses.
    """
    pct = _pnl_pct_after_close(logger, 'SHORT', entry=100.0, exit_price=90.0, leverage=1)
    assert pct == pytest.approx(10.0)


def test_short_loser_has_negative_pnl_pct(logger):
    pct = _pnl_pct_after_close(logger, 'SHORT', entry=100.0, exit_price=110.0, leverage=1)
    assert pct == pytest.approx(-10.0)


def test_long_winner_has_positive_pnl_pct(logger):
    pct = _pnl_pct_after_close(logger, 'LONG', entry=100.0, exit_price=110.0, leverage=1)
    assert pct == pytest.approx(10.0)


def test_leverage_multiplies_pnl_pct(logger):
    """Le rendement est celui des fonds engagés, pas du notionnel."""
    pct = _pnl_pct_after_close(logger, 'LONG', entry=100.0, exit_price=110.0, leverage=5)
    assert pct == pytest.approx(50.0)


def test_close_trade_on_unknown_symbol_is_a_noop(logger):
    """Aucune position ouverte : la fermeture ne doit rien casser."""
    logger.close_trade('DOGEUSDT', exit_price=1.0, pnl=0.0)


# --------------------------------------------------------------------------
# Cohérence de la configuration
# --------------------------------------------------------------------------

def test_configured_leverage_within_risk_cap():
    """config.yaml ne doit pas déclarer un levier supérieur au plafond de risque."""
    from src.config import config
    leverage = int(config.get('trading.leverage', 1))
    max_leverage = int(config.get('risk.max_leverage', 5))
    assert leverage <= max_leverage, (
        f"trading.leverage={leverage} dépasse risk.max_leverage={max_leverage}"
    )


def test_risk_limits_are_positive_and_bounded():
    from src.config import config
    assert 0 < float(config.get('risk.max_risk_per_trade_pct', 1.5)) <= 100
    assert 0 < float(config.get('risk.max_total_position_pct', 33.3)) <= 100
    assert 0 < float(config.get('risk.stop_trading_on_drawdown_pct', 10.0)) <= 100
    assert int(config.get('risk.max_consecutive_losses', 6)) >= 1


def test_risk_manager_reads_configured_limits():
    """Le RiskManager doit refléter config.yaml, pas ses propres valeurs par défaut."""
    from src.config import config
    from src.risk.manager import RiskManager
    rm = RiskManager()
    assert rm.max_leverage == int(config.get('risk.max_leverage', 5))
    assert rm.max_consecutive_losses == int(config.get('risk.max_consecutive_losses', 6))
    assert rm.stop_trading_drawdown_pct == float(
        config.get('risk.stop_trading_on_drawdown_pct', 10.0)
    )


# --------------------------------------------------------------------------
# Vecteur de features du modèle ML
# --------------------------------------------------------------------------

MODEL_PATH = 'models/prophet_lgb_BTCUSDT.pkl'
_model_missing = not os.path.exists(MODEL_PATH)


@pytest.mark.skipif(_model_missing, reason="modèle ML absent")
def test_missing_features_are_silently_zero_filled():
    """Caractérise un comportement dangereux, pour qu'il ne change pas par accident.

    `_prepare_features` complète toute feature absente par 0.0 sans avertir. Le
    modèle attend plus de 80 features : un appelant qui en fournit une poignée
    obtient une prédiction d'apparence normale, calculée sur des zéros.

    Ce test échouera si un garde de couverture est ajouté — ce qui est
    souhaitable, et le test devra alors être mis à jour en conséquence.
    """
    from src.models.prophet_model import ProphetMLModel

    model = ProphetMLModel(MODEL_PATH)
    expected = model.feature_names or []
    assert len(expected) > 50, "le modèle doit attendre un vecteur large"

    vector = model._prepare_features({'rsi': 30.0})
    assert list(vector.columns) == list(expected)

    filled = (vector.iloc[0] == 0.0).sum()
    coverage = 1 - filled / len(expected)
    assert coverage < 0.10, (
        f"couverture de features {coverage:.1%} — la prédiction repose sur des zéros"
    )


@pytest.mark.skipif(_model_missing, reason="modèle ML absent")
def test_prepare_features_is_order_stable():
    """L'ordre des colonnes doit suivre l'ordre d'entraînement, pas celui du dict."""
    from src.models.prophet_model import ProphetMLModel

    model = ProphetMLModel(MODEL_PATH)
    names = model.feature_names or []
    forward = model._prepare_features({n: i for i, n in enumerate(names)})
    reversed_input = model._prepare_features({n: i for i, n in reversed(list(enumerate(names)))})
    assert list(forward.columns) == list(reversed_input.columns) == list(names)
