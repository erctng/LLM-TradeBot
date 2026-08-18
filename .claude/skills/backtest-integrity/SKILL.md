---
name: backtest-integrity
description: Détection des biais de backtest (look-ahead, survivorship, repainting, overfitting) et validation robuste (walk-forward, purged K-fold, embargo, Deflated Sharpe, PBO). À charger dès qu'on écrit ou modifie un backtest, qu'on optimise des paramètres, qu'on compare des stratégies, ou qu'on touche src/backtest/, optimize_backtest.py, compare_strategies.py, run_multi_symbol_backtest.py.
version: "1.0.0"
---

# Backtest Integrity

Un backtest optimiste est pire qu'aucun backtest : il autorise à risquer du capital réel.
Ce skill sert à casser un backtest avant que le marché ne s'en charge.

## Périmètre dans ce repo

`src/backtest/engine.py` (`BacktestEngine`, `BacktestConfig`), `data_replay.py`,
`portfolio.py`, `precision.py`, `optimize_backtest.py`, `compare_strategies.py`.
Le moteur modélise déjà frais maker/taker, slippage, funding rate et marge de maintenance —
c'est au-dessus de la moyenne. Les risques résiduels sont donc surtout **informationnels**
et **statistiques**, pas de coût.

## 1. Look-ahead bias — les points de fuite

À vérifier ligne par ligne dans toute boucle de replay :

- **Bougie courante non close.** Décider sur `df.iloc[-1]` alors que la bougie est en
  cours = utiliser un close futur. La décision à `t` ne peut lire que des bougies closes
  à `t`. Dans `data_replay`, vérifier que la fenêtre est `[:t]` exclusive et non `[:t+1]`.
- **Exécution au close de la bougie de signal.** Si le signal naît du close de `t`,
  l'exécution réaliste est à l'**open de `t+1`**, pas au close de `t`.
- **Stop/TP touchés dans la même bougie.** Si high et low franchissent SL et TP dans la
  même bougie, l'ordre de déclenchement est inconnu. Convention prudente obligatoire :
  **supposer que le SL est touché en premier**. L'hypothèse inverse gonfle le win rate
  de plusieurs points.
- **Indicateurs recalculés sur toute la série.** `ta` et pandas calculent sur le
  DataFrame complet ; si le DataFrame contient le futur, l'indicateur fuit. Voir
  `signal-features`.
- **Normalisation / scaling global.** Un `StandardScaler` fitté sur tout l'historique
  injecte la moyenne future. Fitter uniquement sur le train.
- **Funding rate futur.** `get_funding_rate_for_settlement(timestamp)` ne doit renvoyer
  que des settlements ≤ timestamp.
- **Paramètres choisis avec la connaissance du résultat.** Le biais le plus fréquent et
  le moins détectable : choisir la période de backtest, le symbole ou le seuil après
  avoir vu la courbe.

## 2. Survivorship & selection bias

Ce bot sélectionne dynamiquement les symboles (`symbol_selector_agent`, `ai500_updater`).
Backtester sur la liste des symboles **actuellement** liquides, c'est backtester sur les
gagnants connus. Les tokens delistés ou effondrés depuis n'apparaissent jamais.

Correctifs : reconstruire l'univers tel qu'il était à chaque date, ou à défaut **déclarer
explicitement** le biais et ne jamais présenter le résultat comme une espérance future.

## 3. Modélisation des coûts — seuils crédibles

| Poste | Valeur réaliste (Binance USDⓈ-M) | Dans le repo |
|---|---|---|
| Taker | 0.04 % (0.036 % avec BNB) | `FeeStructure.binance_vip0` ✔ |
| Maker | 0.02 % | ✔ |
| Slippage market cap large | 2–5 bps | `slippage=0.001` (10 bps) — conservateur ✔ |
| Slippage altcoin / forte vol | 15–50 bps, non linéaire en taille | non modélisé ✘ |
| Funding | ~0.01 % / 8 h, jusqu'à ±0.75 % en stress | ✔ |
| Latence décision LLM | plusieurs secondes à minutes | **non modélisé** ✘ |

Le point aveugle sérieux ici est la **latence LLM** : un cycle multi-agents prend du temps
réel pendant lequel le prix bouge. Le backtest exécute instantanément. Sur des signaux
courts, ce delta suffit à annuler l'edge. Modéliser un décalage d'exécution de N secondes
et mesurer la sensibilité du Sharpe à N est le test le plus révélateur de ce bot.

## 4. Overfitting — le vrai ennemi

`optimize_backtest.py` explore un espace de paramètres. Chaque essai supplémentaire
augmente le meilleur Sharpe observé **même sur du bruit pur**.

- **Deflated Sharpe Ratio** : corrige le Sharpe du nombre d'essais `N` et de la
  non-normalité. Avec 100 combinaisons testées, un Sharpe de 1.5 peut être non
  significatif. Toujours logger le nombre d'essais.
- **PBO (Probability of Backtest Overfitting)** par CSCV : découper en blocs, comparer le
  rang IS vs OOS. PBO > 0.5 ⇒ la sélection de paramètres est du bruit.
- **Surface de paramètres** : préférer un plateau large à un pic. Un optimum isolé est un
  artefact. `analyze_parameter_impact` dans `analytics.py` est le bon endroit pour le voir.
- **Règle de budget** : ≥ 30 trades OOS par paramètre libre optimisé. En dessous, ne pas
  optimiser — fixer les paramètres par raisonnement économique.

## 5. Protocole de validation

```
Walk-forward ancré (le seul protocole acceptable pour valider un changement) :
  train [t0, t1] → optimise → test [t1, t2] (jamais retouché)
  puis fenêtre glissante, agrégation des seules périodes OOS
```

Pour tout modèle ML (meta-labeling) : **purged K-fold + embargo**. Les labels de trading
se chevauchent dans le temps (un trade ouvert à `t` se clôture à `t+h`), donc un K-fold
naïf met de l'information du test dans le train. Purger les échantillons dont l'horizon
recouvre le fold de test, plus un embargo de ~1 % de la série. Voir `meta-labeling`.

Garder **un hold-out final jamais regardé** (ex. les 3 derniers mois). Il ne sert qu'une
fois, pour la décision go/no-go. S'il est consulté deux fois, il est brûlé.

## 6. Tests de robustesse à faire passer

Un changement n'est validé que s'il survit à tous :

- [ ] Sensibilité paramètres : ±20 % sur chaque paramètre ⇒ dégradation graduelle, pas de falaise
- [ ] Multi-symboles : l'edge tient sur ≥ 3 symboles non corrélés (`run_multi_symbol_backtest.py`)
- [ ] Multi-périodes : bull, bear et range séparément — pas seulement l'agrégat
- [ ] Stress coûts : doubler frais et slippage ⇒ reste rentable
- [ ] Décalage d'exécution : +1 bougie de retard ⇒ l'edge survit
- [ ] Randomisation : mélanger l'ordre des trades ⇒ MDD observé dans la distribution attendue
- [ ] Monte Carlo bootstrap sur les trades ⇒ IC 95 % du Sharpe final ne contient pas 0

## Anti-patterns rédhibitoires

- Rapporter le meilleur run d'une optimisation comme performance attendue
- Backtest sans frais "pour voir le signal pur" puis décision prise sur ce chiffre
- Ajuster la stratégie après avoir vu l'OOS (c'est de l'IS déguisé)
- Equity curve trop lisse : quasi toujours un look-ahead, chercher avant de célébrer
- Win rate > 75 % avec RR > 1.5 : combinaison quasi inexistante en crypto, chercher le bug
