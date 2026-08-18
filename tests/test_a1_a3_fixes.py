"""
Correctifs A1 et A3 issus de l'observation continue de 24 h.

A1 : l'agent trigger recevait un `volume_ratio` 15 m sous le nom du RVOL 5 m.
A3 : une ronde d'entraînement poursuivait ses symboles pendant un bannissement.
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import pytest

from src.runners.semantic_analysis_runner import SemanticAnalysisRunner


# --------------------------------------------------------------------------
# A1 — provenance du RVOL transmis à l'agent trigger
# --------------------------------------------------------------------------

def _context(four_layer=None, df_5m=None, df_15m=None):
    dfs = {}
    if df_5m is not None:
        dfs['5m'] = df_5m
    if df_15m is not None:
        dfs['15m'] = df_15m
    return types.SimpleNamespace(
        symbol='BTCUSDT',
        four_layer_result=four_layer if four_layer is not None else {},
        processed_dfs=dfs,
    )


def _volumes(values):
    return pd.DataFrame({'volume': values})


def test_uses_the_value_layer4_actually_gated_on():
    """Le récit donné au modèle doit citer le même chiffre que le gate."""
    ctx = _context(four_layer={'trigger_rvol': 2.07})
    assert SemanticAnalysisRunner._trigger_rvol(ctx) == pytest.approx(2.07)


def test_never_falls_back_to_the_15m_volume_ratio():
    """Le défaut d'origine : une grandeur d'un autre timeframe.

    `volume_ratio` 15 m vaut ici 0,5 alors que le RVOL 5 m vaut 4,0. Retomber
    sur le premier faisait conclure « volume insuffisant » à l'agent pendant
    que L4 voyait une rupture franche.
    """
    df_15m = pd.DataFrame({'volume_ratio': [0.5] * 30})
    df_5m = _volumes([1.0] * 8 + [4.0])

    ctx = _context(four_layer={'trigger_rvol': None}, df_5m=df_5m, df_15m=df_15m)
    assert SemanticAnalysisRunner._trigger_rvol(ctx) == pytest.approx(4.0)


def test_recomputes_on_5m_when_the_detector_is_disabled():
    df_5m = _volumes([10.0] * 8 + [25.0])
    ctx = _context(four_layer={'trigger_rvol': None}, df_5m=df_5m)
    assert SemanticAnalysisRunner._trigger_rvol(ctx) == pytest.approx(2.5)


def test_neutral_value_when_no_data_at_all():
    """Ni signal de rupture, ni signal d'assèchement."""
    assert SemanticAnalysisRunner._trigger_rvol(_context()) == 1.0


def test_short_5m_history_is_not_extrapolated():
    ctx = _context(four_layer={'trigger_rvol': None}, df_5m=_volumes([5.0, 6.0]))
    assert SemanticAnalysisRunner._trigger_rvol(ctx) == 1.0


def test_zero_average_volume_does_not_divide_by_zero():
    ctx = _context(four_layer={'trigger_rvol': None}, df_5m=_volumes([0.0] * 8 + [3.0]))
    assert SemanticAnalysisRunner._trigger_rvol(ctx) == 1.0


def test_unparseable_gate_value_falls_back_rather_than_raising():
    df_5m = _volumes([2.0] * 8 + [6.0])
    ctx = _context(four_layer={'trigger_rvol': 'n/a'}, df_5m=df_5m)
    assert SemanticAnalysisRunner._trigger_rvol(ctx) == pytest.approx(3.0)


def test_call_site_no_longer_reads_the_15m_frame():
    """Garde de couplage sur la ligne exacte du défaut."""
    src = open('src/runners/semantic_analysis_runner.py').read()
    trigger_block = src[src.index('trigger_data = {'):src.index('trigger_data = {') + 400]
    assert "processed_dfs['15m']['volume_ratio']" not in trigger_block


# --------------------------------------------------------------------------
# A3 — interruption de la ronde d'entraînement sur bannissement
# --------------------------------------------------------------------------

def test_rate_limit_error_is_exported():
    from src.models.prophet_model import RateLimitedError
    assert issubclass(RateLimitedError, RuntimeError)


@pytest.mark.parametrize('message', [
    'APIError(code=-1003): Way too many requests',
    'Way too many requests; IP banned until 1700000000',
])
def test_fetch_raises_on_rate_limit_instead_of_returning_none(message):
    """Un bannissement est un état global, pas un échec propre au symbole.

    Le renvoyer comme un `None` indifférencié empêchait l'appelant de faire la
    différence entre « ce symbole n'a pas de données » et « l'API nous a
    coupés », donc d'arrêter la ronde.
    """
    from src.models.prophet_model import ProphetAutoTrainer, RateLimitedError

    trainer = ProphetAutoTrainer.__new__(ProphetAutoTrainer)

    class _Boom:
        def futures_klines(self, **kwargs):
            raise Exception(message)

    trainer.client = types.SimpleNamespace(client=_Boom())
    trainer.training_days = 1
    trainer.symbol = 'BTCUSDT'

    with pytest.raises(RateLimitedError):
        trainer._fetch_data('BTCUSDT')


def test_ordinary_fetch_failure_still_returns_none():
    """Une panne banale ne doit pas interrompre les autres symboles."""
    from src.models.prophet_model import ProphetAutoTrainer

    trainer = ProphetAutoTrainer.__new__(ProphetAutoTrainer)

    class _Boom:
        def futures_klines(self, **kwargs):
            raise Exception('connection reset by peer')

    trainer.client = types.SimpleNamespace(client=_Boom())
    trainer.training_days = 1
    trainer.symbol = 'BTCUSDT'

    assert trainer._fetch_data('BTCUSDT') is None


def test_training_loop_aborts_the_round_on_a_ban():
    """Garde de couplage : la boucle doit rompre, pas enchaîner.

    Poursuivre ajoutait des requêtes pendant un ban actif, ce qui ne pouvait
    que le prolonger — cinq échecs observés sur 36 tentatives en 24 h.
    """
    src = open('src/models/prophet_model.py').read()
    loop = src[src.index('while self._running:'):]
    handler = loop.index('except RateLimitedError')
    assert 'break' in loop[handler:handler + 900], (
        "la ronde ne s'interrompt pas sur bannissement"
    )
    assert 'log.error' in loop[handler:handler + 900], (
        "l'échec doit remonter en erreur, pas en warning : un modèle qui cesse "
        "d'être réentraîné en silence est un angle mort"
    )


def test_helper_is_callable_the_way_production_calls_it():
    """Appel via une instance, pas via la classe.

    Les tests précédents passaient par `SemanticAnalysisRunner._trigger_rvol(ctx)`,
    ce qui fonctionne même si le décorateur `@staticmethod` manque. Le code
    appelle `self._trigger_rvol(context)` : sans le décorateur, `self` compte
    comme premier argument et l'appel lève. Le défaut est passé en production.
    """
    runner = SemanticAnalysisRunner.__new__(SemanticAnalysisRunner)
    ctx = _context(four_layer={'trigger_rvol': 1.8})
    assert runner._trigger_rvol(ctx) == pytest.approx(1.8)


def test_run_kept_its_instrumentation():
    """Le helper s'était inséré entre `@log_run` et `run`, privant `run` de son
    décorateur — une instrumentation perdue en silence."""
    import inspect
    src = inspect.getsource(SemanticAnalysisRunner)
    run_at = src.index('async def run(')
    assert '@log_run' in src[max(0, run_at - 120):run_at], (
        "`run` a perdu son décorateur @log_run"
    )
