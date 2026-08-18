---
name: live-reconciliation
description: Réconciliation live vs backtest et surveillance de production — détection de dérive de performance, schéma de logging des trades, shadow mode, paper trading, alerting, post-mortem. À charger dès qu'on parle de "live", "production", "VPS", "dérive", "le bot sous-performe", "shadow", "paper trading", "monitoring", ou qu'on touche src/api/vps_telemetry.py, src/monitoring/, scripts/local_dashboard.py.
version: "1.0.0"
---

# Live vs Backtest — réconciliation et monitoring

L'écart entre performance backtestée et performance réelle est l'information la plus
précieuse produite par un bot en production. Encore faut-il pouvoir le mesurer.

## Infrastructure existante

| Élément | Emplacement |
|---|---|
| Bot | VPS `49.13.235.188`, conteneur Docker `llm-tradebot` |
| Base live | `/root/LLM-TradeBot/data/analytics/trading.db` (VPS) |
| API télémétrie | `src/api/vps_telemetry.py`, port 8085 |
| Dashboard local | `scripts/local_dashboard.py` |
| Logs cycles | `/root/LLM-TradeBot/logs/` |
| Doc | `TELEMETRY_AND_LOGS_REFERENCE.md` |

**La base locale du repo est vide** : toute analyse de performance réelle doit lire la base
du VPS. Ne jamais conclure depuis la copie locale.

## 1. Blocage actuel : le schéma `trades` est insuffisant

```sql
trades(id, open_time, close_time, symbol, side, entry_price, exit_price,
       quantity, leverage, pnl, pnl_pct, status)
```

Manquent, et chacun bloque une analyse précise :

| Colonne | Débloque |
|---|---|
| `fees_paid`, `funding_paid` | KPI **nets** — sans ça le live est brut et le backtest net : incomparables |
| `stop_loss`, `take_profit` (à l'entrée) | R-multiple, expectancy en R |
| `exit_reason` (sl/tp/trailing/manuel/liquidation) | savoir **pourquoi** on perd |
| `mae`, `mfe` | stops trop serrés ? sorties trop précoces ? |
| `decision_id` (FK vers `decisions`) | attribution agent → résultat |
| `decision_price`, `slippage_bps` | qualité d'exécution |
| `regime` | décomposition de l'edge par régime |

**C'est le chantier n°1.** Toute amélioration de KPI est spéculative tant que ces champs
n'existent pas. Migration additive (colonnes nullables), rétrocompatible.

## 2. Les cinq causes d'écart live/backtest

Diagnostiquer dans cet ordre — de la plus fréquente à la plus rare :

1. **Coûts sous-estimés** : slippage réel > hypothèse, funding ignoré, maker supposé mais
   taker en réalité. Comparer le slippage p50/p95 mesuré à `BacktestConfig.slippage`.
2. **Latence** : le backtest exécute instantanément, le live après un cycle LLM complet.
   Comparer `decision_price` et `fill_price`.
3. **Look-ahead résiduel** dans le backtest ⇒ voir `backtest-integrity`.
4. **Changement de régime** : le backtest couvrait un marché différent. Vérifier avant de
   conclure à un bug — c'est souvent la vraie explication.
5. **Bug d'implémentation** : logique live ≠ logique backtest. Le test décisif : rejouer les
   décisions live dans le moteur de backtest sur les mêmes données et comparer trade à trade.

## 3. Protocole de dérive

Ne pas juger sur l'impression. Cadre statistique :

- **Fenêtre glissante de 20–30 trades** sur win rate, expectancy en R, slippage moyen
- Bandes de contrôle : moyenne backtest ± 2σ. Une sortie de bande sur 2 fenêtres
  consécutives déclenche une revue, pas une modification immédiate.
- **Ne jamais modifier la stratégie sur < 30 trades.** Le drawdown normal d'une stratégie
  saine est indistinguable d'une stratégie cassée sur un petit échantillon. La sur-réaction
  aux pertes récentes est la première cause de destruction d'un bot rentable.
- Distinguer **dégradation de l'edge** (win rate/expectancy en baisse) de **dégradation
  d'exécution** (slippage/latence en hausse). Les remèdes sont opposés.

## 4. Séquence de déploiement obligatoire

```
backtest OOS  →  paper trading (testnet Binance, ≥ 2 semaines, ≥ 30 trades)
              →  shadow mode (décisions loggées, non exécutées, sur données live)
              →  live à taille réduite (25 % du sizing cible, ≥ 50 trades)
              →  live à taille pleine
```

Le **shadow mode** est le meilleur outil de ce projet : il permet d'évaluer un nouveau
prompt, un nouvel agent ou un nouveau seuil de meta-labeling en conditions réelles, sans
risque. Le moteur de backtest a déjà un `shadow_portfolio` — l'équivalent live vaut
largement l'effort.

Aucune étape n'est sautée, y compris pour "un petit changement de paramètre".

## 5. Alerting

À déclencher immédiatement (canal push, pas seulement un log) :

- Position ouverte non réconciliée avec l'exchange
- Ordre SL/TP orphelin détecté
- Drawdown au-delà du seuil de circuit breaker
- Provider LLM en échec ⇒ cycles sans décision
- Bot silencieux : aucun cycle depuis N minutes (heartbeat) — **la panne la plus dangereuse
  est celle qui ne produit aucune erreur**, avec des positions ouvertes non surveillées
- Écart solde exchange ↔ solde interne > tolérance

À logger sans alerter : décision skip, clamp du RiskManager (mais compter et alerter si le
taux augmente), latence élevée ponctuelle.

## 6. Post-mortem

Après toute perte anormale ou comportement inattendu, écrire dans `docs/postmortems/` :
contexte de marché, décision et `llm_raw_output` complet, votes de chaque agent, validation
risque, exécution réelle, écart vs attendu, cause racine, correctif, **test de non-régression
ajouté**. Le corpus de post-mortems est ce qui fait progresser un bot plus vite que
n'importe quelle optimisation de paramètres.

## Checklist mise en production

- [ ] Testé sur testnet, pas seulement en backtest
- [ ] Réconciliation position exchange ↔ interne au démarrage
- [ ] Heartbeat et alerting actifs
- [ ] Rollback possible : version précédente déployable en une commande
- [ ] Limites de risque vérifiées en conditions réelles avant montée en taille
- [ ] Baseline KPI enregistrée avant le changement, pour comparaison ultérieure
