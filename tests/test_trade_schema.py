"""
Schéma des trades et KPI nets de coûts.

`all_trades.csv` est la source unique de vérité des trades. Son schéma s'est
élargi pour porter les coûts, le risque initial et le motif de sortie. Comme
`save_trade` ajoute les lignes en mode append sans en-tête, un fichier resté à
l'ancien schéma se corromprait au premier ajout : ces tests verrouillent la
migration et les KPI qui en dépendent.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import pytest

from src.analytics.performance import compute_kpis, load_closed_trades
from src.utils.data_saver import DataSaver

V1_COLUMNS = DataSaver._TRADE_COLUMNS_V1


@pytest.fixture
def saver(tmp_path):
    return DataSaver(base_dir=str(tmp_path), mode='live')


@pytest.fixture
def trades_csv(saver):
    path = os.path.join(saver.dirs['trades'], 'all_trades.csv')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _legacy_rows(n=3):
    return pd.DataFrame([{
        'record_time': f'2026-05-2{i} 00:00:00',
        'open_cycle': i, 'close_cycle': 0, 'action': 'OPEN_LONG',
        'symbol': 'BTCUSDT', 'price': 100.0 + i, 'quantity': 1.0,
        'cost': 100.0 + i, 'exit_price': 110.0 + i, 'pnl': 10.0,
        'confidence': 70, 'status': 'CLOSED',
    } for i in range(n)])[V1_COLUMNS]


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------

def test_migration_preserves_every_legacy_row(saver, trades_csv):
    _legacy_rows(5).to_csv(trades_csv, index=False)

    saver._migrate_trades_schema(trades_csv)
    after = pd.read_csv(trades_csv)

    assert len(after) == 5
    assert list(after.columns)[:len(V1_COLUMNS)] == V1_COLUMNS
    assert after['pnl'].sum() == pytest.approx(50.0)


def test_migration_leaves_new_columns_empty_not_zero(saver, trades_csv):
    """Vide et zéro ne sont pas la même information.

    Un frais inconnu ne doit pas être lu comme un frais nul, sinon les KPI
    « nets » de l'historique seraient identiques aux KPI bruts sans que rien ne
    le signale.
    """
    _legacy_rows(3).to_csv(trades_csv, index=False)
    saver._migrate_trades_schema(trades_csv)

    after = pd.read_csv(trades_csv)
    assert after['fees_paid'].isna().all()
    assert after['exit_reason'].isna().all()


def test_migration_writes_a_backup(saver, trades_csv):
    _legacy_rows(2).to_csv(trades_csv, index=False)
    saver._migrate_trades_schema(trades_csv)

    backup = f'{trades_csv}.v1.bak'
    assert os.path.exists(backup)
    assert list(pd.read_csv(backup).columns) == V1_COLUMNS


def test_migration_is_idempotent(saver, trades_csv):
    _legacy_rows(2).to_csv(trades_csv, index=False)
    saver._migrate_trades_schema(trades_csv)
    first = pd.read_csv(trades_csv)

    saver._migrate_trades_schema(trades_csv)
    assert pd.read_csv(trades_csv).shape == first.shape


def test_migration_on_missing_file_is_a_noop(saver, tmp_path):
    saver._migrate_trades_schema(str(tmp_path / 'absent.csv'))


def test_append_after_migration_does_not_shift_columns(saver, trades_csv):
    """Le scénario de corruption : ajouter une ligne large à un fichier étroit."""
    _legacy_rows(2).to_csv(trades_csv, index=False)

    saver.save_trade({
        'action': 'OPEN_SHORT', 'symbol': 'ETHUSDT', 'price': 3000.0,
        'quantity': 2.0, 'cost': 6000.0, 'pnl': 0.0, 'confidence': 80,
        'status': 'OPEN', 'side': 'SHORT', 'leverage': 3,
        'stop_loss': 3090.0, 'take_profit': 2880.0, 'regime': 'ranging',
    })

    df = pd.read_csv(trades_csv)
    assert len(df) == 3
    row = df.iloc[-1]
    assert row['symbol'] == 'ETHUSDT'
    assert row['side'] == 'SHORT'
    assert float(row['stop_loss']) == pytest.approx(3090.0)
    assert float(row['quantity']) == pytest.approx(2.0)


# --------------------------------------------------------------------------
# Clôture
# --------------------------------------------------------------------------

def _open_one(saver, **extra):
    payload = {
        'action': 'OPEN_LONG', 'symbol': 'BTCUSDT', 'price': 100.0,
        'quantity': 1.0, 'cost': 100.0, 'pnl': 0.0, 'confidence': 70,
        'status': 'OPEN', 'side': 'LONG', 'exit_price': 0.0,
    }
    payload.update(extra)
    saver.save_trade(payload)


def test_close_records_reason_and_accumulates_fees(saver, trades_csv):
    _open_one(saver, fees_paid=0.4)

    assert saver.update_trade_exit(
        symbol='BTCUSDT', exit_price=110.0, pnl=10.0, exit_time='12:00',
        close_cycle=3, exit_reason='take_profit', fees_paid=0.44,
        funding_paid=0.1, fees_estimated=True, mae=-2.0, mfe=12.0,
    ) is True

    row = pd.read_csv(trades_csv).iloc[-1]
    assert row['status'] == 'CLOSED'
    assert row['exit_reason'] == 'take_profit'
    # Frais d'ouverture + frais de fermeture.
    assert float(row['fees_paid']) == pytest.approx(0.84)
    assert float(row['funding_paid']) == pytest.approx(0.1)
    assert float(row['mae']) == pytest.approx(-2.0)
    assert float(row['mfe']) == pytest.approx(12.0)


def test_close_does_not_overwrite_costs_when_not_supplied(saver, trades_csv):
    _open_one(saver, fees_paid=0.4)

    saver.update_trade_exit(
        symbol='BTCUSDT', exit_price=110.0, pnl=10.0, exit_time='12:00',
    )

    row = pd.read_csv(trades_csv).iloc[-1]
    assert float(row['fees_paid']) == pytest.approx(0.4), (
        "les frais d'ouverture ont été écrasés par la clôture"
    )


def test_close_on_unknown_symbol_returns_false(saver, trades_csv):
    _open_one(saver)
    assert saver.update_trade_exit('DOGEUSDT', 1.0, 0.0, '12:00') is False


# --------------------------------------------------------------------------
# KPI nets
# --------------------------------------------------------------------------

def _closed_frame(tmp_path, fees=None, funding=None, n=4):
    """Passe par le vrai chargeur : `compute_kpis` consomme sa sortie.

    Construire le DataFrame à la main contournerait `load_closed_trades`, qui
    ajoute `timestamp` et normalise `side` — les tests ne vérifieraient alors
    pas le chemin réellement emprunté par la télémétrie.
    """
    rows = []
    for i in range(n):
        row = {
            'record_time': f'2026-06-0{i + 1} 00:00:00', 'symbol': 'BTCUSDT',
            'action': 'OPEN_LONG', 'status': 'CLOSED',
            'price': 100.0, 'exit_price': 105.0, 'quantity': 1.0,
            'cost': 100.0, 'pnl': 5.0 if i % 2 == 0 else -3.0,
        }
        if fees is not None:
            row['fees_paid'] = fees
        if funding is not None:
            row['funding_paid'] = funding
        rows.append(row)

    path = tmp_path / 'closed.csv'
    pd.DataFrame(rows).to_csv(path, index=False)
    return load_closed_trades(str(path))


def test_empty_cost_columns_are_not_mistaken_for_zero_cost(tmp_path):
    """Le piège introduit par la migration.

    Après élargissement du schéma, les colonnes de coût existent sur tout
    l'historique mais restent vides. Se contenter de tester leur présence ferait
    annoncer des KPI « nets » identiques aux bruts.
    """
    frame = _closed_frame(tmp_path)
    frame['fees_paid'] = pd.NA
    frame['funding_paid'] = pd.NA

    k = compute_kpis(frame)
    assert k.cost_data_available is False
    assert k.cost_coverage_pct == 0.0
    assert k.net_pnl is None
    assert any('brut' in note.lower() for note in k.notes)


def test_net_pnl_deducts_fees_and_funding(tmp_path):
    k = compute_kpis(_closed_frame(tmp_path, fees=0.5, funding=0.1, n=4))

    assert k.cost_data_available is True
    assert k.cost_coverage_pct == pytest.approx(100.0)
    assert k.total_fees == pytest.approx(2.0)
    assert k.total_funding == pytest.approx(0.4)
    assert k.net_pnl == pytest.approx(k.total_pnl - 2.4)
    assert k.net_expectancy == pytest.approx(k.net_pnl / k.closed_trades)


def test_partial_cost_coverage_is_flagged(tmp_path):
    frame = _closed_frame(tmp_path, fees=0.5, n=4)
    frame.loc[frame.index[:2], 'fees_paid'] = pd.NA

    k = compute_kpis(frame)
    assert k.cost_coverage_pct == pytest.approx(50.0)
    assert any('50%' in note or 'seulement' in note for note in k.notes)


def test_cost_drag_is_expressed_against_gross_profit(tmp_path):
    k = compute_kpis(_closed_frame(tmp_path, fees=0.5, funding=0.0, n=4))
    assert k.cost_drag_pct is not None
    assert k.cost_drag_pct > 0
