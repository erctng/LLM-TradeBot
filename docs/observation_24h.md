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
| 12:03 | 7 | 109 | −53,45 | 0 | RAS. 0 réinit agents. Cumul : 13 cycles, **0 ouverture**, 121 décisions `wait`. Cohérent avec A1. |
| 12:33 | 6 | 109 | −53,45 | 0 | RAS. Stable, rien de neuf. |
| 13:03 | 6 | 109 | −53,45 | 0 | RAS. Identique au tick précédent. |
| 13:33 | 6 | 109 | −53,45 | 0 | Fausse alerte « 401 » — défaut de l'outil, corrigé (voir ci-dessous). Bot RAS. |
| 14:03 | 6 | 109 | −53,45 | 0 | RAS. Premier tick sans faux positif depuis le durcissement des motifs. |
| 14:33 | 6 | 109 | −53,45 | 0 | RAS. 3 h 40 d'observation, ~43 cycles cumulés, toujours **0 ouverture**. |
| 15:03 | 7 | 109 | −53,45 | 0 | RAS. |
| 15:33 | 6 | 109 | −53,45 | 0 | RAS. |
| 16:03 | 6 | 109 | −53,45 | 0 | RAS. Décisions/symbole à 15 au lieu de 18 : effet de bord de la fenêtre, les 3 symboles sont bien traités. |
| 16:33 | 6 | 109 | −53,45 | 0 | RAS. Retour à 18 décisions/symbole, confirmant l'effet de bord du tick précédent. |
| 17:03 | 6 | 109 | −53,45 | 0 | RAS. |
| 17:33 | 6 | 109 | −53,45 | 0 | RAS. Chaîne du « 0 trade » entièrement expliquée (voir ci-dessous). |
| 18:03 | 6 | 109 | −53,45 | 0 | RAS. |
| 18:33 | 6 | 109 | −53,45 | 0 | RAS. Mi-parcours approchant : 7 h 40 sans incident hors A1. |
| 19:03 | 6 | 109 | −53,45 | 0 | RAS. |
| 19:33 | 6 | 109 | −53,45 | 0 | RAS. |
| 20:03 | 6 | 109 | −53,45 | 0 | RAS. |
| 20:33 | 6 | 109 | −53,45 | 0 | RAS. 10 h d'uptime, cadence parfaitement régulière depuis le départ. |
| 21:03 | 6 | 109 | −53,45 | 0 | RAS. |
| 21:33 | 6 | 109 | −53,45 | 0 | RAS. |
| 22:03 | 6 | 109 | −53,45 | 0 | RAS. |
| 22:33 | 6 | 109 | −53,45 | 0 | RAS. Mi-parcours : 11 h 36 écoulées sur 24 h, aucun incident nouveau depuis A1. |
| 23:03 | 6 | 109 | −53,45 | 0 | RAS. |

---

## Pourquoi zéro trade en 6 h 40 — chaîne complète

Cumul sur 69 cycles / 207 décisions :

| Étape | Résultat |
|---|---|
| Couche L4 (déclencheur 5 m) | 19 passages sur 207 — **9,2 %** |
| Verdict des 4 couches | 19 `SHORT`, 188 `WAIT` |
| Décision finale du LLM | **1** `OPEN_SHORT`, 206 `WAIT` — confiance moyenne 38 % |
| Audit de risque | le seul `OPEN_SHORT` **bloqué** |
| Trades écrits | **0** |

Le blocage final n'est pas un défaut : *« ETHUSDT空头连续亏损3次，触发冷却 »* —
cooldown déclenché après 3 pertes consécutives sur les shorts ETH. Le garde-fou
fait exactement son travail, et il vise précisément le côté qui concentre 78 %
des pertes historiques.

Trois filtres en série expliquent donc l'absence de trade, et **un seul est
défectueux** :

1. **L4 bloque 91 % des cycles** — sain sur le principe, mais le narratif fourni
   au LLM sur cette couche est faux (A1).
2. **Le LLM rejette 18 des 19 signaux restants**, à 38 % de confiance moyenne.
   Cohérent avec A1 : il lit un volume relatif sous-évalué.
3. **L'audit de risque bloque le dernier** — comportement correct et souhaitable.

Conclusion provisoire : le bot ne trade pas parce qu'il est *correctement*
prudent sur deux étages et *incorrectement* informé sur un troisième. Corriger
A1 est le seul levier qui change quelque chose sans toucher aux garde-fous.

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

## Faux positif du relevé — motifs numériques nus

13:33Z — le relevé a signalé « authentification API refusée ». Vérification :
zéro erreur d'authentification réelle dans les logs. Le motif `'401'` matchait
les trois chiffres n'importe où, ici à l'intérieur d'un timestamp
(`13:30:44.914012` → `4012`).

Même classe de défaut que le `'402'` nu corrigé au premier tick. Les motifs
portent désormais sur le texte propre à chaque message (`鉴权失败`,
`Unauthorized`, `Invalid API-key`, `Way too many requests`…) et plus jamais sur
un code HTTP isolé. Un relevé qui crie au loup sur des timestamps est pire
qu'inutile sur 48 ticks.

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
