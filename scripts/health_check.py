#!/usr/bin/env python3
"""
Relevé de santé du bot en fonctionnement continu.

Sortie compacte et stable, destinée à être comparée d'un relevé à l'autre
pendant une observation longue. Chaque anomalie est préfixée par [ANOMALIE].

Usage:
    python3 scripts/health_check.py            # relevé courant
    python3 scripts/health_check.py --since 30 # fenêtre de log en minutes
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

CONTAINER = os.getenv('BOT_CONTAINER', 'llm-tradebot')
TRADES_CSV = 'data/live/execution/trades/all_trades.csv'

# Motifs de log qui traduisent une panne réelle, pas du bruit.
#
# Jamais de code HTTP nu : « 401 » et « 402 » apparaissent dans les timestamps,
# les prix et les probabilités, et déclenchaient de fausses alertes. Chaque
# motif doit contenir du texte propre au message d'erreur.
ERROR_PATTERNS = [
    ('Payment Required', 'crédit LLM épuisé'),
    ('鉴权失败', 'authentification API refusée'),
    ('Unauthorized', 'authentification API refusée'),
    ('Invalid API-key', 'clé API invalide'),
    ('Signature for this request', 'signature de requête invalide'),
    ('LLM decision failed', 'décision LLM en échec, repli sur les règles'),
    ('Agent error', 'agent en erreur'),
    ('Traceback', 'exception non gérée'),
    ('CIRCUIT_BREAKER', 'coupe-circuit armé'),
    ('Risk gate check failed', 'porte de risque en erreur'),
    ('Way too many requests', 'bannissement pour excès de requêtes'),
]

# Dégradations connues et déjà diagnostiquées : signalées pour information,
# pas comptées comme anomalies nouvelles. Les remonter à chaque relevé
# noierait les vraies découvertes.
KNOWN_DEGRADED = [
    ('Quant API 请求失败: 402', 'API Quant (nofxos.ai) sans crédit — OI/netflow indisponibles'),
]

# Un cycle est identifié par son numéro. `[Step 3/5]` se déclenche une fois par
# symbole analysé : le compter comme un cycle gonfle le total d'un facteur égal
# au nombre de symboles suivis, et rend la cadence illisible.
CYCLE_RE = r'Cycle #(\d+)'
DECISION_MARKER = '[Step 3/5]'

STATE_FILE = 'data/.health_baseline'


def _docker_logs(minutes: int) -> str:
    try:
        out = subprocess.run(
            ['docker', 'logs', CONTAINER, '--since', f'{minutes}m'],
            capture_output=True, text=True, timeout=120,
        )
        return out.stdout + out.stderr
    except Exception as e:
        return f'__DOCKER_UNAVAILABLE__ {e}'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', type=int, default=30, help='fenêtre de log (minutes)')
    args = parser.parse_args()

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')
    print(f'=== RELEVÉ {now} (fenêtre {args.since} min) ===')
    anomalies = []

    # 1. Conteneur
    status = subprocess.run(
        ['docker', 'ps', '--filter', f'name={CONTAINER}', '--format', '{{.Status}}'],
        capture_output=True, text=True,
    ).stdout.strip()
    print(f'conteneur      : {status or "ABSENT"}')
    if not status:
        anomalies.append('conteneur arrêté')
    elif 'unhealthy' in status.lower():
        anomalies.append('conteneur unhealthy')

    restarts = subprocess.run(
        ['docker', 'inspect', CONTAINER, '--format', '{{.RestartCount}}'],
        capture_output=True, text=True,
    ).stdout.strip()
    print(f'redémarrages   : {restarts or "?"}')
    if restarts.isdigit() and int(restarts) > 0:
        anomalies.append(f'{restarts} redémarrage(s) du conteneur')

    logs = _docker_logs(args.since)
    if logs.startswith('__DOCKER_UNAVAILABLE__'):
        anomalies.append('logs docker illisibles')
        logs = ''

    # 2. Activité : le bot avance-t-il ?
    import re

    cycle_ids = set(re.findall(CYCLE_RE, logs))
    cycles = len(cycle_ids)
    decisions = logs.count(DECISION_MARKER)
    print(f'cycles         : {cycles}')
    print(f'décisions/sym. : {decisions}')
    if cycles == 0:
        anomalies.append(f'aucun cycle en {args.since} min — bot figé ou arrêté')
    else:
        # Cadence attendue : ~un cycle toutes les 5 minutes.
        expected = max(args.since // 5, 1)
        if cycles > expected * 2:
            anomalies.append(
                f'{cycles} cycles pour {expected} attendus — cadence trop rapide, '
                f'cycles qui se chevauchent ?'
            )

    # 3. Boucle à vide (le défaut observé à 1 Hz)
    reinit = logs.count('run_continuous')
    print(f'réinit agents  : {reinit}')
    if reinit > max(cycles * 3, 10):
        anomalies.append(f'{reinit} réinitialisations pour {cycles} cycles — boucle à vide')

    # 4. Erreurs
    for pattern, label in ERROR_PATTERNS:
        n = logs.count(pattern)
        if n:
            print(f'  {pattern:<24} x{n}  ({label})')
            anomalies.append(f'{label} — {n} occurrence(s)')

    for pattern, label in KNOWN_DEGRADED:
        n = logs.count(pattern)
        if n:
            print(f'  [connu] {label} x{n}')

    # 5. Trades et KPI
    try:
        from src.analytics.performance import compute_kpis, load_closed_trades
        import pandas as pd

        raw = pd.read_csv(TRADES_CSV) if os.path.exists(TRADES_CSV) else pd.DataFrame()
        closed = load_closed_trades(TRADES_CSV)
        print(f'lignes trades  : {len(raw)}')
        print(f'trades clos    : {len(closed)}')

        if len(closed):
            k = compute_kpis(closed)
            print(f'win rate       : {k.win_rate_pct:.1f}%  (n={k.closed_trades})')
            print(f'expectancy     : {k.expectancy:+.4f}')
            print(f'PnL total      : {k.total_pnl:+.2f}')
            print(f'max DD         : {k.max_drawdown_pct:.2f}%')
            print(f'couverture coûts: {k.cost_coverage_pct:.0f}%')
            if k.net_pnl is not None:
                print(f'PnL net        : {k.net_pnl:+.2f}')
            if k.max_drawdown_pct > 10:
                anomalies.append(f'drawdown {k.max_drawdown_pct:.1f}% au-delà du seuil de 10%')

        # Colonnes neuves : ne juger que les lignes écrites depuis le début de
        # l'observation. L'historique antérieur à la migration de schéma les a
        # forcément vides — le signaler à chaque relevé noierait le signal.
        baseline = 0
        if os.path.exists(STATE_FILE):
            try:
                baseline = int(open(STATE_FILE).read().strip())
            except ValueError:
                baseline = 0
        else:
            baseline = len(raw)
            with open(STATE_FILE, 'w') as fh:
                fh.write(str(baseline))

        fresh = raw.iloc[baseline:] if len(raw) > baseline else raw.iloc[0:0]
        print(f'nouvelles lignes: {len(fresh)} (depuis le début de l\'observation)')

        # A2 : deux chemins d'écriture contournaient le schéma. Corrigé dans le
        # dépôt (e74c074) mais volontairement non redéployé avant la fin de
        # l'observation. Tant que le conteneur tourne l'ancienne image, le
        # défaut est attendu — le signaler comme neuf à chaque tick masquerait
        # une vraie régression.
        schema_gap_expected = os.path.exists('data/.a2_deferred')

        if len(fresh):
            gaps = []
            for col in ('side', 'stop_loss', 'fees_paid', 'regime'):
                if col in fresh.columns and fresh[col].notna().sum() == 0:
                    gaps.append(f'colonne {col} non renseignée sur les nouvelles lignes')
            if 'price' in fresh.columns:
                zero_price = (pd.to_numeric(fresh['price'], errors='coerce').fillna(0) == 0).sum()
                if zero_price == len(fresh):
                    gaps.append("prix d'entrée à 0 sur toutes les nouvelles lignes")

            if gaps and schema_gap_expected:
                print(f'  [connu] A2 — schéma incomplet, correctif non déployé ({len(gaps)} champs)')
            else:
                anomalies.extend(gaps)
    except Exception as e:
        anomalies.append(f'calcul KPI impossible : {e}')

    # 6. Verdict
    print()
    if anomalies:
        print(f'[ANOMALIE] {len(anomalies)} point(s) :')
        for a in anomalies:
            print(f'  - {a}')
    else:
        print('RAS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
