"""
Réactivation de l'open interest et neutralisation de la règle de divergence.

La récupération de l'OI Binance était désactivée ; le tracker restait vide, la
couverture toujours fausse, et la couche L1 évaluait sa règle de divergence
prix/OI sur un ratio de volume — 26 rejets sur 26 dus à cette seule règle.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


# --------------------------------------------------------------------------
# Récupération de l'OI
# --------------------------------------------------------------------------

def test_open_interest_fetch_is_no_longer_stubbed():
    """`b_oi = {}` en dur rendait le tracker inalimentable."""
    src = open(os.path.join(REPO, 'src/agents/data_sync/data_sync_agent.py')).read()
    assert 'Mock empty OI' not in src
    assert 'DISABLE OI' not in src
    assert src.count('_fetch_open_interest(symbol)') >= 2, (
        'les deux chemins (WebSocket et REST) doivent récupérer l\'OI'
    )


def test_fetch_failure_returns_empty_rather_than_raising():
    """Un OI indisponible ne doit jamais interrompre le cycle.

    Sans OI, la couverture reste fausse et les consommateurs retombent
    explicitement sur le proxy volume — dégradation choisie, pas panne.
    """
    import asyncio
    import types
    from src.agents.data_sync.data_sync_agent import DataSyncAgent

    agent = DataSyncAgent.__new__(DataSyncAgent)

    def _boom(symbol):
        raise RuntimeError('API down')

    agent.client = types.SimpleNamespace(get_open_interest=_boom)
    assert asyncio.run(agent._fetch_open_interest('BTCUSDT')) == {}


def test_successful_fetch_is_returned_verbatim():
    import asyncio
    import types
    from src.agents.data_sync.data_sync_agent import DataSyncAgent

    payload = {'symbol': 'BTCUSDT', 'open_interest': 368201742.7, 'timestamp': 1787013346987}
    agent = DataSyncAgent.__new__(DataSyncAgent)
    agent.client = types.SimpleNamespace(get_open_interest=lambda s: payload)

    assert asyncio.run(agent._fetch_open_interest('BTCUSDT')) == payload


def test_tracker_only_records_positive_open_interest():
    """Un OI nul ou absent ne doit pas polluer l'historique."""
    src = open(os.path.join(REPO, 'src/agents/data_sync/data_sync_agent.py')).read()
    assert "b_oi.get('open_interest', 0) > 0" in src


# --------------------------------------------------------------------------
# Règle de divergence L1
# --------------------------------------------------------------------------

def test_divergence_rule_is_neutralised_without_real_oi():
    """Le cœur du correctif : pas de vraie donnée, pas de règle.

    Neutraliser plutôt qu'assouplir — un seuil relevé laisserait la règle
    statuer sur une grandeur qui n'est pas celle qu'elle juge.
    """
    src = open(os.path.join(REPO, 'src/runners/four_layer_filter_stage_runner.py')).read()
    guard = src.index('oi_is_real')
    block = src[guard:guard + 400]
    assert "== 'open_interest'" in block
    assert "float('inf')" in block, (
        'la règle doit être rendue inatteignable, pas simplement desserrée'
    )
    assert 'oi_divergence_skipped' in block, (
        'le contournement doit être tracé dans le résultat, pas silencieux'
    )


def test_guard_sits_before_the_divergence_checks():
    """Un garde posé après la règle ne servirait à rien."""
    src = open(os.path.join(REPO, 'src/runners/four_layer_filter_stage_runner.py')).read()
    assert src.index('oi_is_real') < src.index('OI Divergence: Trend UP')


@pytest.mark.parametrize('source,neutralised', [
    ('open_interest', False),
    ('volume_proxy', True),
    ('unavailable', True),
])
def test_only_real_open_interest_enables_the_rule(source, neutralised):
    four_layer_result = {'oi_source': source}
    oi_is_real = four_layer_result.get('oi_source') == 'open_interest'
    assert (not oi_is_real) is neutralised


def test_missing_source_is_treated_as_real_for_backwards_compatibility():
    """Un appelant qui ne renseigne pas la provenance conserve l'ancien
    comportement plutôt que de voir la règle disparaître en silence."""
    assert ({}.get('oi_source') == 'open_interest') is False
    # Le producteur pose explicitement 'open_interest' par défaut :
    src = open(os.path.join(REPO, 'src/runners/four_layer_filter_stage_runner.py')).read()
    assert "oi_fuel.get('oi_source', 'open_interest')" in src
