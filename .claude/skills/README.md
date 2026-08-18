# Skills projet — LLM-TradeBot

Skills de domaine chargés automatiquement par Claude Code selon le contexte de la tâche.
Ils encodent l'expertise trading quantitatif spécifique à ce bot (futures Binance,
architecture multi-agents LLM) et signalent les pièges présents dans ce repo.

| Skill | Déclenché par | Couvre |
|---|---|---|
| `trading-kpi` | KPI, Sharpe, drawdown, télémétrie, `backtest/metrics.py` | Définitions correctes, pièges de calcul, KPI manquants |
| `backtest-integrity` | backtest, optimisation, comparaison de stratégies | Look-ahead, survivorship, overfitting, walk-forward |
| `risk-sizing` | position size, levier, stop, `risk/manager.py` | Sizing par le stop, liquidation, Kelly, circuit breakers |
| `execution-quality` | ordres, API Binance, `execution/engine.py` | Filtres exchange, idempotence, slippage, latence |
| `signal-features` | indicateurs, features, régime, `features/` | Repainting, multi-timeframe, stationnarité, microstructure |
| `meta-labeling` | filtre ML, `agents/meta/` | Triple barrier, purged CV, calibration, seuil par EV |
| `llm-agent-trading` | agents, prompts, `src/llm/`, consensus | Sortie structurée, anti-hallucination, attribution par agent |
| `live-reconciliation` | live, VPS, dérive, monitoring | Écart live/backtest, schéma de logs, shadow mode, alerting |

## Chantiers prioritaires identifiés lors de la création de ces skills

1. **Enrichir la table `trades`** (frais, funding, SL/TP d'entrée, `exit_reason`, MAE/MFE,
   `decision_id`, slippage, régime). Tout le reste en dépend — voir `live-reconciliation` §1.
2. **Corriger les KPI live** : le Sharpe de `vps_telemetry.py` est un Sharpe par trade non
   annualisé, le drawdown est calculé sur PnL réalisé et non sur equity — voir `trading-kpi`.
3. **Modéliser la latence LLM** dans le backtest : point aveugle le plus susceptible
   d'expliquer un écart live/backtest — voir `backtest-integrity` §3.
4. **Versionner les prompts** modifiés par `meta_optimizer_agent`, sinon l'historique de
   performance est ininterprétable — voir `llm-agent-trading` §2.
5. **Contrôle de risque au niveau portefeuille** (corrélation entre positions), absent du
   `RiskManager` actuel — voir `risk-sizing`.
