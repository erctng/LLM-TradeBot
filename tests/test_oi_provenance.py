"""
Provenance de la mesure d'open interest.

L'agent trend recevait un ratio de volume sous l'étiquette « Open Interest » et
concluait sur du positionnement net alors qu'il ne voyait que de la rotation.
Ces tests verrouillent trois propriétés : le vrai OI est utilisé quand
l'historique le permet, le repli sur le volume est explicite, et une couverture
d'historique insuffisante n'est jamais présentée comme une variation 24 h.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from src.agents.trend.trend_agent_llm import TrendAgentLLM
from src.utils.oi_tracker import OITracker


# --------------------------------------------------------------------------
# Couverture de l'historique
# --------------------------------------------------------------------------

@pytest.fixture
def tracker(tmp_path):
    return OITracker(data_dir=str(tmp_path))


def _record_span(tracker, symbol, hours, points=5):
    """Enregistre `points` mesures réparties sur `hours` heures."""
    now_ms = time.time() * 1000
    for i in range(points):
        ts = now_ms - (hours * 3600 * 1000) * (1 - i / (points - 1))
        tracker.record(symbol, oi_value=1000.0 * (1 + 0.1 * i), timestamp=ts)


def test_coverage_is_zero_without_history(tracker):
    assert tracker.coverage_hours('BTCUSDT') == 0.0
    assert tracker.has_coverage('BTCUSDT', hours=24) is False


def test_short_history_does_not_claim_24h_coverage(tracker):
    """La régression silencieuse : deux heures d'historique lues comme 24 h.

    `get_change_pct` retombe sur l'enregistrement le plus ancien quand la
    fenêtre n'est pas couverte. Il renvoie donc un chiffre plausible et faux —
    d'où la nécessité de tester la couverture avant de s'y fier.
    """
    _record_span(tracker, 'BTCUSDT', hours=2)

    assert tracker.coverage_hours('BTCUSDT') == pytest.approx(2.0, abs=0.1)
    assert tracker.has_coverage('BTCUSDT', hours=24) is False
    # La valeur est calculable, mais elle ne vaut pas 24 h.
    assert tracker.get_change_pct('BTCUSDT', hours=24) != 0.0


def test_full_history_reports_coverage(tracker):
    _record_span(tracker, 'BTCUSDT', hours=26)

    assert tracker.has_coverage('BTCUSDT', hours=24) is True
    assert tracker.get_change_pct('BTCUSDT', hours=24) == pytest.approx(40.0, abs=1.0)


# --------------------------------------------------------------------------
# Formulation du prompt
# --------------------------------------------------------------------------

def _section(source, oi_change=2.5, fuel='MODERATE FUEL'):
    return TrendAgentLLM._fuel_section({'oi_source': source}, oi_change, fuel)


def test_real_open_interest_is_labelled_as_such():
    text = _section('open_interest')
    assert 'Open Interest' in text
    assert 'PROXY' not in text


def test_volume_proxy_is_never_presented_as_open_interest():
    """Le cœur du correctif."""
    text = _section('volume_proxy')
    assert 'PROXY' in text
    assert 'Volume' in text
    assert 'NOT open interest' in text
    # Aucune ligne ne doit annoncer une mesure d'open interest.
    assert 'OI Change (24h)' not in text


def test_saturated_proxy_value_is_flagged():
    """200 % est le plafond de l'écrêtage, pas une mesure.

    C'est la valeur qui avait fait écrire au modèle « a massive 200% surge in
    open interest signaling strong fuel ».
    """
    text = _section('volume_proxy', oi_change=200.0, fuel='STRONG FUEL')
    assert 'écrêtée' in text or 'clipped' in text.lower()


def test_unavailable_source_tells_the_model_to_stay_silent():
    text = _section('unavailable')
    assert 'Unavailable' in text
    assert 'Do not reference' in text


@pytest.mark.parametrize('source', ['open_interest', 'volume_proxy', 'unavailable'])
def test_section_is_non_empty_for_every_source(source):
    assert _section(source).strip()


def test_missing_source_defaults_to_open_interest_wording():
    """Rétrocompatibilité : un appelant qui n'envoie pas la provenance."""
    text = TrendAgentLLM._fuel_section({}, 1.2, 'MODERATE FUEL')
    assert 'Open Interest' in text


# --------------------------------------------------------------------------
# Métadonnée réinjectée dans le contexte des autres agents
# --------------------------------------------------------------------------

def _meta(source, oi_change=200.0, fuel='STRONG'):
    return TrendAgentLLM._fuel_metadata(
        {'oi_source': source}, {'oi_change': oi_change, 'fuel': fuel}
    )


def test_proxy_metadata_never_exposes_an_oi_key():
    """La clé `oi_change` est le seul indice dont disposent Bull/Bear.

    Portant un ratio de volume, elle leur faisait écrire « Open Interest surged
    +200% » alors qu'aucune donnée d'open interest n'avait été mesurée.
    """
    meta = _meta('volume_proxy')
    assert 'oi_change' not in meta
    assert 'oi_fuel' not in meta
    assert meta['volume_vs_avg_pct'] == 200.0
    assert meta['oi_available'] is False


def test_real_oi_metadata_keeps_the_oi_keys():
    meta = _meta('open_interest', oi_change=4.2)
    assert meta['oi_change'] == 4.2
    assert meta['oi_fuel'] == 'STRONG'
    assert meta['oi_available'] is True


def test_unavailable_source_exposes_no_measurement():
    meta = _meta('unavailable')
    assert meta == {'oi_available': False}
