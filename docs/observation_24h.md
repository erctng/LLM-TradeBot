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

> Note d'outillage (11:03) : le relevé comptait les `[Step 3/5]` comme des cycles,
> alors qu'ils se déclenchent une fois **par symbole**. Les 3 et 6 « cycles » des
> premiers ticks valaient en réalité 1 et 2. Corrigé : le comptage porte désormais
> sur les identifiants `Cycle #N` distincts, et les décisions par symbole sont
> affichées à part. Les deux premières lignes du tableau ont été rectifiées.
