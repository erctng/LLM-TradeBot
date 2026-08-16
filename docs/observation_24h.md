# Observation continue — 24 h

**Début** : 2026-08-16 10:56:38Z · **Fin visée** : 2026-08-17 ~11:00Z
**Configuration** : conteneur `llm-tradebot`, mode `--test` (simulé, aucun ordre réel),
`--interval 5`, `--autostart`, code au commit `f0282e2`.
**Relevé** : `python3 scripts/health_check.py --since 35`, toutes les 30 min (job cron `3193fc6f`).

Baseline à l'ouverture : 221 lignes de trades, 109 clôturés, PnL −53,45, win rate 41,3 %.
Ces chiffres proviennent de l'historique antérieur ; toute évolution ci-dessous est
imputable à l'observation en cours.

---

## Anomalies déjà connues (non recomptées à chaque tick)

| # | Anomalie | État |
|---|---|---|
| K1 | API Quant `nofxos.ai` renvoie 402 — OI et netflow live indisponibles | Ouverte, compte sans crédit |
| K2 | Modèle Prophet : AUC validation 0,5623 contre 0,7373 en entraînement | Ouverte, surapprentissage |
| K3 | Historique antérieur à la migration : colonnes de coûts vides | Attendu, se résorbe avec les nouveaux trades |

## Anomalies corrigées avant le lancement

| # | Anomalie | Correctif |
|---|---|---|
| F1 | Conteneur jamais en trading — attente d'un clic Start inexistant en déploiement serveur | `--autostart` (`08753ce`) |
| F2 | Reconstruction de tous les agents ~1×/s, aucun cycle exécuté, 180 lignes de log/min | Normalisation avant comparaison de config (`08753ce`) |
| F3 | Deux instances possibles sur les mêmes volumes montés, sans verrou | `flock` sur `data/.bot.lock` (`08753ce`) |
| F4 | `--headless --mode continuous` structurellement incapable de trader | Avertissement explicite + `--autostart` (`08753ce`) |

---

## Journal

| Heure (UTC) | Cycles | Trades clos | PnL | Nouvelles lignes | Anomalies |
|---|---|---|---|---|---|
| 10:57 | 1 | 109 | −53,45 | 0 | RAS — état initial |
| 11:03 | 2 | 109 | −53,45 | 0 | RAS. Cadence conforme : 2 cycles en 9 min à `--interval 5`. Aucun trade ouvert (décisions `wait`). |
| 11:33 | 6 | 109 | −53,45 | 0 | **A1 découverte** (ci-dessous). Bot sain par ailleurs, 0 redémarrage. |

---

## A1 — L'agent trigger reçoit un volume 15 m sous le nom du RVOL 5 m

**Découverte** : 11:33Z, en cherchant pourquoi L4 ne passe que 4 fois sur 18.

**Symptôme** : le RVOL journalisé n'a jamais dépassé 0,8 sur 30 mesures consécutives
(BTC, ETH, SOL — trois actifs liquides). Un volume relatif oscille normalement
autour de 1,0. Sept valeurs à « 0,0 » après arrondi.

**Cause racine** — [`semantic_analysis_runner.py:97`](../src/runners/semantic_analysis_runner.py) :

```python
'rvol': context.processed_dfs['15m']['volume_ratio'].iloc[-1]
```

Trois erreurs cumulées sur une ligne :

1. **Mauvais timeframe** — lecture du dataframe **15 m** alors que la couche L4
   est explicitement le déclencheur **5 m**. Le prompt de l'agent annonce
   « Current 5m setup ».
2. **Mauvaise grandeur** — `volume_ratio` vaut `volume / SMA20(volume)`, une
   comparaison sur 20 barres. Le RVOL du déclencheur est calculé par
   `calculate_rvol()` sur **8 barres de 5 m**. Deux quantités distinctes
   circulent sous le même nom `rvol`.
3. **Le garde et le récit divergent** — la décision L4 utilise le bon
   `calculate_rvol` (`four_layer_result['trigger_rvol']`), mais le LLM raisonne
   sur l'autre chiffre. Le gate et l'explication ne parlent pas de la même chose.

**Mesure comparative** (ETHUSDT, données enregistrées par le bot) :

| Grandeur | Valeur |
|---|---|
| `volume_ratio` 15 m — ce que reçoit l'agent | 0,44 à 0,84 |
| RVOL 5 m sur 8 barres — ce qui pilote L4 | **2,07** |

**Conséquence** : l'agent conclut « volume insuffisant, pas de déclencheur »
pendant que la couche L4 voit un volume plus de deux fois supérieur à sa moyenne.
Observé en sortie : *« Volume is elevated at 1.4x average but fails to meet the
1.5x breakout threshold »* — un seuil de rupture 5 m appliqué à un ratio 15 m.

**Impact sur l'objectif de rendement** : L4 est la couche qui bloque
pratiquement toutes les entrées (4 passages sur 18 ; L1-L3 passent 17/18). Le
narratif fourni au LLM la concernant est faux, donc l'arbitrage final se fait
sur une information erronée.

**État** : non corrigé — le correctif modifie les entrées d'un agent et donc les
décisions de trading, décision laissée à l'utilisateur.

---

## Incident d'observation

11:33Z — bannissement d'IP Binance testnet (`-1003`) provoqué par mes propres
requêtes de diagnostic en parallèle du bot. **Le bot n'a pas été affecté**
(0 occurrence dans ses logs) et le ban s'est levé en moins d'une minute.
Leçon : interroger les données déjà enregistrées par le bot plutôt que l'API
pendant qu'il tourne.

> Note d'outillage (11:03) : le relevé comptait les `[Step 3/5]` comme des cycles,
> alors qu'ils se déclenchent une fois **par symbole**. Les 3 et 6 « cycles » des
> premiers ticks valaient en réalité 1 et 2. Corrigé : le comptage porte désormais
> sur les identifiants `Cycle #N` distincts, et les décisions par symbole sont
> affichées à part. Les deux premières lignes du tableau ont été rectifiées.
