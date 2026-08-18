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
| 23:33 | 7 | 109 | −53,45 | **1** | **A2 découverte** — première ligne écrite, colonnes neuves vides. |
| 00:03 | 6 | 109 | −53,45 | 1 | A2 persiste (correctif non déployé). Bot passé en gestion de position : analyse concentrée sur BTCUSDT seul, comportement normal. |
| 00:33 | 7 | 109 | −53,45 | 1 | RAS. A2 basculé en dégradation connue le temps du report — sentinelle `data/.a2_deferred`, à supprimer après redéploiement. |
| 01:03 | 7 | 109 | −53,45 | 1 | **A3 découverte** — réentraînement Prophet sauté sur les 3 symboles (rate limit). |
| 01:33 | 6 | 109 | −53,45 | 1 | Pas de récidive d'A3 (0 sur 20 min). Relevé enrichi de l'horodatage des erreurs, la fenêtre de 35 min faisant réalerter un même incident au tick suivant. |
| 02:03 | 7 | 109 | −53,45 | 1 | RAS. A3 sorti de la fenêtre, aucune récidive. |
| 02:33 | 6 | **110** | **−54,76** | 1 | RAS. **Premier aller-retour complet** : clôture du SHORT BTCUSDT à −1,31. Premier PnL net calculé. Confirme A2 (voir ci-dessous). |
| 03:03 | 7 | 110 | −54,76 | 1 | RAS. Position clôturée, retour au balayage des 3 symboles (17 décisions). |
| 03:33 | 6 | 110 | −54,76 | 1 | RAS. |
| 04:03 | 6 | 110 | −54,76 | 1 | RAS. |
| 04:33 | 6 | 110 | −54,76 | 1 | RAS. |
| 05:03 | 7 | 110 | −54,76 | 1 | RAS. |
| 05:33 | 6 | 110 | −54,76 | 1 | RAS. |
| 06:03 | 6 | 110 | −54,76 | 1 | RAS. |
| 06:33 | 6 | 110 | −54,76 | 1 | RAS. |
| 07:03 | 6 | 110 | −54,76 | 1 | RAS. |
| 07:33 | 6 | 110 | −54,76 | 1 | RAS. |
| 08:03 | 6 | 110 | −54,76 | 1 | RAS. |
| 08:33 | 6 | 110 | −54,76 | 1 | RAS. |
| 09:03 | 6 | 110 | −54,76 | 1 | RAS. |
| 09:33 | 6 | 110 | −54,76 | 1 | **A3 récidive** à 09:06 (2 occurrences). Intermittent, non aggravé — voir fréquence ci-dessous. |
| 10:03 | 6 | 110 | −54,76 | 1 | RAS. A3 sorti de la fenêtre sans nouvelle occurrence. |
| 10:33 | 6 | 110 | −54,76 | 1 | RAS. Dernier tick de routine — 23 h 37 écoulées, échéance des 24 h à 10:56:38Z. |
| **11:03** | 6 | 110 | −54,76 | 1 | **Clôture.** RAS. Boucle arrêtée (job `3193fc6f`), correctif A2 redéployé et vérifié, sentinelle supprimée. |

---

## Clôture — 11:03Z

**Durée** : 24 h 07 (10:56:38Z → 11:03Z). **255 cycles**, 0 redémarrage,
conteneur `healthy` de bout en bout.

| Mesure | Valeur |
|---|---|
| Cycles exécutés | 255 |
| Décisions | 683 `WAIT`, 3 `OPEN_SHORT`, 7 `CLOSE_SHORT` |
| Couche L4 | 48 passages sur 693 — **6,9 %** |
| Blocages par l'audit de risque | 7 |
| Allers-retours complets | **1** (SHORT BTCUSDT, −1,31) |
| Erreurs rate limit | 5 |
| Échecs LLM / tracebacks | **0 / 0** |

KPI de sortie : 110 trades clos, PnL −54,76, win rate 40,9 %,
espérance −0,4978, max drawdown 4,85 %.

**Redéploiement A2** : image reconstruite, `build_trade_record` présent dans les
trois fichiers de l'image, constructeur vérifié en conteneur — `price`, `side`,
`leverage`, `stop_loss`, `take_profit`, `fees_paid`, `decision_price`,
`slippage_bps`, `regime` tous renseignés, aucune colonne manquante. Sentinelle
`data/.a2_deferred` supprimée : la détection complète est réactivée.

**Restent ouverts** : K1 (API Quant sans crédit), K2 (AUC validation 0,5623).

---

## Suite — A1 et A3 corrigés et déployés (17 août, 23:5xZ)

| Anomalie | Correctif | Commit |
|---|---|---|
| A1 | L'agent trigger reçoit `four_layer_result['trigger_rvol']`, la valeur que L4 a réellement évaluée. Repli recalculé sur le 5 m quand le détecteur est désactivé, valeur neutre 1,0 si l'historique est trop court ou le volume moyen nul. | `933eaf4` |
| A3 | `_fetch_data` lève `RateLimitedError` sur `-1003`, la ronde s'interrompt au premier bannissement, l'échec remonte en `ERROR`. | `933eaf4` |
| Régression introduite par A1 | Le helper s'était inséré entre `@log_run` et `run`, privant `run` de son instrumentation et faisant lever l'appel en production. | `57ef79b` |

**Leçon supplémentaire** : les tests d'A1 appelaient
`SemanticAnalysisRunner._trigger_rvol(ctx)` **sur la classe**, ce qui fonctionne
même sans `@staticmethod`. La production appelle `self._trigger_rvol(context)`.
Les 13 tests passaient pendant que le code était cassé — un test doit emprunter
le même chemin d'appel que le code. Deux tests ajoutés : appel via instance, et
vérification que `run` conserve son décorateur.

**Vérification post-déploiement** : 0 erreur, repli validé sur données réelles
(0,119 calculé sur du 5 m SOLUSDT), RVOL transmis désormais issu de la bonne
source. Sur les 4 premiers échantillons — 0,2 / 0,3 / 0,7 / 1,0 — **rien ne
permet encore de conclure à un changement de comportement** : c'est l'objet du
test de plusieurs jours à venir.

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

## A2 — Deux chemins d'écriture contournaient le schéma

**Découverte** : 23:33Z, à la toute première ligne de trade produite pendant
l'observation (cycle 130, `OPEN_SHORT` BTCUSDT, simulé).

**Symptôme** : la ligne sort avec `price = 0.0` et `side`, `leverage`,
`stop_loss`, `take_profit`, `fees_paid`, `decision_price`, `regime` tous vides —
précisément les champs que l'élargissement du schéma devait porter. Seuls `cost`
et `cycle_id` étaient renseignés.

**Fausse piste écartée** : le conteneur exécutait bien le code corrigé
(`entry_field='price'` et `_infer_side` présents dans l'image, vérifié par
`docker exec`).

**Cause racine** : deux appels supplémentaires à `save_trade` dans
`multi_agent_trading_bot.py` (lignes 693 et 742) court-circuitent complètement
le runner d'exécution et construisaient leur propre dictionnaire — tous deux
avec la clé `entry_price`, qui n'est pas une colonne. `save_trade` complétait
donc `price` par son défaut 0.0. Corriger le runner seul les avait laissés
intacts.

**Correctif** (`e74c074`) : les trois sites passent par
`DataSaver.build_trade_record`, qui déduit le côté depuis l'action, estime les
frais taker sur le notionnel, calcule le slippage face au prix de décision et
remplit chaque colonne déclarée. Un garde de couplage vérifie que chaque appel
`save_trade` est adossé à une construction, pour qu'un quatrième site ne puisse
plus diverger en silence.

**Leçon** : le premier trade réel a invalidé en une ligne ce que 13 h de relevés
« RAS » n'avaient pas pu tester. Un chemin d'écriture ne se valide qu'en
l'empruntant.

### Confirmation par le premier aller-retour complet (02:33Z)

Le SHORT BTCUSDT ouvert au cycle 130 s'est clôturé au cycle 165 à −1,31.
La ligne finale sépare nettement les deux chemins :

| Champ | Valeur | Chemin |
|---|---|---|
| `close_cycle`, `exit_price`, `pnl`, `status` | corrects | clôture ✅ |
| `exit_reason` | `signal` | clôture ✅ |
| `fees_paid`, `fees_estimated` | 0,10986 / 1 | clôture ✅ |
| `price`, `side`, `leverage`, `stop_loss`, `decision_price`, `regime` | vides | ouverture ❌ |

Le correctif de `update_trade_exit` est donc bien déployé et opérationnel ; seul
le chemin d'ouverture reste à redéployer. Diagnostic A2 confirmé sans ambiguïté.

**Conséquence chiffrée sur les coûts** : les frais enregistrés valent exactement
`exit_price × quantité × 0,0004` = 0,10986. **Seule la jambe de sortie est
comptée** — celle d'entrée n'a jamais été écrite, faute de prix d'entrée. Les
frais sont donc sous-estimés d'environ 50 %, et le premier « PnL net » affiché
(−54,87) est optimiste. Le R-multiple reste incalculable : ni prix d'entrée ni
stop.

**Déploiement** : décision prise de **ne pas redéployer avant la fin des 24 h**,
pour ne pas casser la continuité de l'observation. Le conteneur continue donc
d'écrire des lignes incomplètes jusqu'à ~11:00Z ; ces lignes sont identifiables
par `price = 0` et resteront inexploitables pour le R-multiple et le slippage.
Redéploiement prévu dans la synthèse finale.

---

## A3 — Le réentraînement du modèle est sauté en silence

**Découverte** : 01:03Z, via le motif `Way too many requests` ajouté lors du
durcissement du relevé à 13:33 — un motif qui n'existait pas au lancement.

**Fait** : à 01:02:34-35, l'auto-entraîneur Prophet a échoué sur les **trois**
symboles avec `APIError(code=-1003): Way too many requests`, puis a journalisé
`数据不足，跳过训练 (当前: 0)` pour chacun. La première requête revient déjà
vide : le bannissement était actif avant le début du lot.

**Ce qui n'est pas en cause** : le rythme de récupération existe bien —
`_fetch_data` découpe en lots de 1000 avec `time.sleep(0.2)` entre chaque
(correctif `c43a97b`). Ce n'est pas une boucle de retry non plus : les trois
appels rapprochés sont trois symboles distincts, pas trois tentatives.

**Les deux vrais défauts** :

1. **Aucune prise en compte du bannissement entre symboles.** Le premier
   `-1003` devrait interrompre toute la ronde d'entraînement. À la place, la
   boucle enchaîne les deux symboles suivants et ajoute des requêtes pendant un
   ban actif, ce qui ne peut que le prolonger.
2. **Dégradation silencieuse.** L'échec ne produit qu'un `WARNING`. Le modèle
   en mémoire reste celui de la ronde précédente et vieillit sans que rien ne le
   signale. Cumulé à K2 (AUC validation 0,5623, à peine au-dessus du hasard),
   un modèle qui cesse d'être réentraîné sans alerte est un angle mort réel.

**Impact observé** : circonscrit. 3 occurrences, toutes dans la fenêtre de
01:02. Le bot continue de cycler normalement (Cycle #149) et conserve son modèle
précédent. Aucune décision de trading n'a été perdue.

### Fréquence mesurée sur 22 h (relevé de 09:33Z)

| Occurrence | Horodatage | Symboles touchés |
|---|---|---|
| 1 | 01:02:34 → 01:02:35 | 3 (burst de 2 s) |
| 2 | 09:06:29 → 09:06:55 | 2 (espacé de 26 s) |

12 rondes d'entraînement sur la période, soit **36 tentatives par symbole, dont
5 échecs — environ 14 %**. Le défaut est donc **intermittent et non aggravé** :
il ne s'auto-entretient pas, et l'espacement plus large de la seconde occurrence
suggère que la récupération avait partiellement progressé avant de buter.

L'horodatage ajouté au relevé à 01:33 a permis de trancher immédiatement entre
récidive et rappel de fenêtre — sans lui, ce tick aurait été ambigu.

**État** : non corrigé, à traiter avec le redéploiement de fin d'observation.
Correctif proposé : interrompre la ronde au premier `-1003` et remonter l'échec
au niveau d'une alerte, pas d'un warning.

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
