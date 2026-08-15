---
name: trading-kpi
description: Calcul, audit et correction des KPI de performance du bot (Sharpe, Sortino, Calmar, Profit Factor, Expectancy, R-multiple, Max Drawdown, Ulcer Index, cost drag). À charger dès qu'on parle de "performance", "KPI", "métriques", "Sharpe", "drawdown", "win rate", "dashboard", "télémétrie", ou qu'on modifie src/backtest/metrics.py, src/backtest/analytics.py ou src/api/vps_telemetry.py.
version: "1.0.0"
---

# Trading KPI — calcul et audit

Les KPI pilotent toutes les décisions d'amélioration du bot. Un KPI faux ne se voit pas :
il produit une conviction fausse. Ce skill impose les définitions correctes et signale les
pièges présents dans ce repo.

## Où vivent les KPI dans ce projet

| Source | Fichier | Portée |
|---|---|---|
| Backtest | `src/backtest/metrics.py` (`MetricsResult`, `PerformanceMetrics`) | complet, sur equity curve |
| Backtest agrégé | `src/backtest/analytics.py` (`BacktestAnalytics`) | comparaison de runs, tendances |
| Live | `src/api/vps_telemetry.py` | simplifié, sur `trades.pnl` |
| Doc | `TELEMETRY_AND_LOGS_REFERENCE.md` §4 | formules de référence — **contient des approximations** |

Base live : `data/analytics/trading.db` (VPS `49.13.235.188`). La copie locale est
généralement vide — ne jamais conclure "0 trade" sans vérifier la base VPS.

## Pièges actifs dans ce repo — à corriger, pas à propager

1. **Sharpe non annualisé.** `vps_telemetry.calculate_sharpe` renvoie `mean(pnl)/std(pnl)`
   par trade. Ce n'est pas un Sharpe ratio, c'est une expectancy normalisée. Un Sharpe
   par trade et un Sharpe annualisé de backtest ne sont **pas comparables**. Toujours
   annoncer la périodicité : `Sharpe(per-trade)` vs `Sharpe(annualized, daily)`.
2. **Drawdown sur PnL cumulé.** La formule `(Peak - cumPnL)/(Capital + Peak)` de la doc
   n'est pas le max drawdown standard. Le MDD se calcule sur la **courbe d'equity
   mark-to-market**, pas sur les PnL réalisés : sinon les positions ouvertes perdantes
   sont invisibles et le MDD est sous-estimé.
3. **`annualized_return` désactivé** dans `metrics.py` (commenté, "misleading for short
   backtests"). C'est le bon réflexe : ne jamais annualiser < 6 mois d'historique.
   Mais alors **Calmar et Sharpe annualisés héritent du même biais** — les afficher avec
   la durée d'échantillon à côté.
4. **La table `trades` ne stocke ni frais, ni funding, ni MAE/MFE, ni `exit_reason`.**
   Les KPI live sont donc bruts alors que le backtest est net (`total_fees_paid`,
   `total_funding_paid`, `total_slippage_cost` dans `portfolio.py`). Comparer les deux
   directement est invalide. Voir le skill `live-reconciliation`.

## Définitions de référence

Sur une série de rendements périodiques `r` (périodicité `P` : 365 pour du daily crypto,
`8760` pour du horaire — le crypto trade 24/7, **jamais 252**).

```
Sharpe      = (mean(r) - rf/P) / std(r, ddof=1) * sqrt(P)
Sortino     = (mean(r) - target) / downside_dev * sqrt(P)
              downside_dev = sqrt(mean(min(r - target, 0)^2))   # dénominateur = N total, pas N négatifs
Calmar      = annualized_return / |max_drawdown_pct|
MAR         = même chose, calculé depuis l'inception
Ulcer Index = sqrt(mean(drawdown_pct^2))                        # pénalise la durée du DD, pas juste la profondeur
Martin      = annualized_return / Ulcer Index
```

Sur les trades :

```
Win rate       = wins / closed_trades
Profit Factor  = sum(gains) / |sum(pertes)|                     # > 1.5 correct, > 2 suspect si N < 100
Expectancy ($) = win_rate*avg_win - (1-win_rate)*avg_loss
Expectancy (R) = mean(pnl_i / risque_initial_i)                 # LE KPI le plus robuste
Payoff / RR    = avg_win / |avg_loss|
Kelly          = win_rate - (1-win_rate)/payoff
```

**R-multiple** : `R = pnl_net / (|entry - stop_loss| * quantity)`. Le risque au
dénominateur est le risque **initialement engagé**, jamais le risque ajusté par un
trailing stop. C'est le seul KPI qui rend comparables des trades de tailles différentes,
et ce bot fait varier `position_size_pct` et `leverage` par décision — donc sans
R-multiple, la moyenne des PnL est un mélange non homogène.

## KPI manquants à ajouter en priorité

Par ordre de valeur ajoutée pour ce bot :

1. **Expectancy en R** + distribution des R (histogramme). Nécessite de persister
   `stop_loss` à l'entrée dans `trades`.
2. **MAE / MFE** (Maximum Adverse / Favorable Excursion) par trade. Répond à
   "mes stops sont-ils trop serrés ?" et "est-ce que je sors trop tôt ?" — les deux
   questions les plus rentables sur un bot qui a déjà un edge.
3. **Cost drag** : `(frais + funding + slippage) / |PnL brut|`. En futures crypto à
   levier avec cycles courts, ce ratio dépasse souvent 30 % et détruit l'edge
   silencieusement. Le backtest le mesure déjà, le live non.
4. **Time in market** et **turnover**. Un Sharpe élevé avec 3 % de temps exposé n'est pas
   comparable à un Sharpe équivalent en permanence exposé.
5. **Décomposition par régime / symbole / heure UTC / agent déclencheur.** L'edge est
   presque toujours concentré. `analytics.py` fait déjà du per-symbol ; étendre au régime
   (`regime_detector_agent`) est le meilleur ratio effort/gain.
6. **Consecutive losses distribution** + probabilité de ruine, pour calibrer le
   `max_consecutive_losses` du `RiskManager` (défaut 3) sur des données plutôt qu'à vue.

## Règles de reporting

- Toujours donner **N** (nombre de trades) à côté de tout KPI. Sous 30 trades clôturés,
  aucun ratio n'est significatif — le dire explicitement plutôt que d'afficher 2 décimales.
- Intervalle de confiance sur le win rate : `± 1.96*sqrt(p(1-p)/N)`. Avec N=20 et p=60 %,
  l'IC est [38 %, 82 %] : c'est indistinguable du hasard.
- Ne jamais comparer des runs de périodes différentes sans normaliser par la performance
  du buy & hold sur la même fenêtre (`alpha` vs `beta` de marché).
- Les KPI nets de frais sont les seuls qui comptent. Un KPI brut doit être étiqueté "brut".

## Checklist avant de déclarer une amélioration

- [ ] KPI recalculés **nets** de frais + funding + slippage
- [ ] N ≥ 30 trades clôturés, sinon marqué "non significatif"
- [ ] Périodicité d'annualisation explicite et cohérente (365j crypto)
- [ ] MDD calculé sur equity mark-to-market, pas sur PnL réalisé
- [ ] Comparaison à la baseline (version précédente + buy & hold) sur période identique
- [ ] Expectancy en R rapportée, pas seulement en $
