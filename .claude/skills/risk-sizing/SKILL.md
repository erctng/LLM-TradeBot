---
name: risk-sizing
description: Dimensionnement de position, levier, stops, et limites de risque portefeuille pour futures crypto (Kelly fractionnaire, vol targeting, ATR stops, distance de liquidation, corrélation, circuit breakers de drawdown). À charger dès qu'on parle de "position size", "levier", "stop loss", "risque", "drawdown", "liquidation", "money management", ou qu'on modifie src/risk/manager.py, src/trading/trading_parameters.py, src/backtest/portfolio.py.
version: "1.0.0"
---

# Risk & Position Sizing

En futures à levier, le sizing détermine la survie ; le signal ne détermine que le rendement.
Un edge positif avec un sizing trop agressif finit à zéro avec probabilité 1.

## État actuel dans ce repo

`src/risk/manager.py` (`RiskManager`) applique des garde-fous par décision :

| Limite | Défaut | Portée |
|---|---|---|
| `max_risk_per_trade_pct` | 1.5 % | par trade |
| `max_total_position_pct` | 30 % | notionnel |
| `max_leverage` | 5 | par trade |
| `max_consecutive_losses` | 3 | circuit breaker |

Ces limites sont **par trade**, appliquées en clamp (`validate_decision` corrige la valeur
au lieu de rejeter). Le manque principal est le niveau **portefeuille** : rien n'empêche
5 positions corrélées à 1.5 % chacune, ce qui est en réalité un unique pari de 7.5 %.

## Hiérarchie des contraintes

Appliquer dans cet ordre, la plus contraignante gagne :

```
1. Risque par trade      : perte au stop ≤ 0.5–1.5 % de l'equity
2. Risque par cluster    : somme des risques corrélés (|ρ| > 0.7) ≤ 2–3 %
3. Exposition brute      : notionnel total / equity ≤ plafond
4. Risque journalier     : perte cumulée du jour ≤ 3 % ⇒ arrêt jusqu'à J+1
5. Circuit breaker DD    : MDD > 10 % ⇒ sizing /2 ; > 20 % ⇒ arrêt et revue
```

En crypto, **presque tout est corrélé à BTC**. Traiter simultanément 4 alts long, c'est
un trade BTC à 4× la taille. Le contrôle de cluster n'est pas optionnel ici.

## Formules

**Sizing par le stop (la seule méthode correcte)** — la taille découle du stop, jamais
l'inverse :

```
risk_amount = equity * risk_pct
qty         = risk_amount / |entry - stop|
notional    = qty * entry
margin      = notional / leverage
```

Le levier n'est **pas** un paramètre de risque : il ne change que la marge immobilisée et
la distance de liquidation. Le risque réel est fixé par `|entry - stop| * qty`. Confondre
les deux est l'erreur la plus coûteuse en futures. Vérifier que `RiskManager` traite bien
`leverage` comme une contrainte de marge et non comme un multiplicateur de risque.

**Distance de liquidation** (à vérifier avant chaque envoi d'ordre) :

```
liq_distance_pct ≈ 100/leverage - maintenance_margin_rate*100
```

À 5×, la liquidation est à ~19 %. Un stop à 3 % est donc largement en amont : bon.
**Règle dure : le stop doit être à ≤ 50 % de la distance de liquidation.** Sinon un wick
liquide la position avant que le stop ne s'exécute — et la liquidation coûte 0.5 % de
pénalité supplémentaire (`liquidation_fee` dans `portfolio.py`).

**Kelly fractionnaire** :

```
f* = win_rate - (1 - win_rate)/payoff
taille = f* / 4     # quart de Kelly, jamais plus
```

Kelly plein maximise la croissance log mais avec des drawdowns de 50 %+ et suppose des
probabilités connues. Elles ne le sont pas : elles sont estimées sur un échantillon fini
et non stationnaire. Sur < 100 trades, l'estimation de `f*` est surtout du bruit — utiliser
un risque fixe.

**Vol targeting** (adapte la taille au régime, très efficace en crypto) :

```
size_mult = vol_cible / vol_réalisée_récente     # clampé [0.25, 2.0]
```

La volatilité BTC varie d'un facteur 4 entre régimes. Un risque fixe en % signifie un
risque effectif variable en réalité. C'est l'amélioration de sizing avec le meilleur ratio
effort/impact pour ce bot, et elle se branche sur `regime_detector_agent`.

## Stops

- **ATR-based, pas en % fixe.** `stop = entry ± k*ATR(14)`, k ∈ [1.5, 3]. Un stop de 2 %
  est serré sur ETH en régime calme et large en pleine capitulation.
- **Chandelier exit** (déjà implémenté dans le repo) : `plus_haut_depuis_entrée - k*ATR`.
  Bon choix pour laisser courir les gagnants sans rendre les gains.
- Un trailing stop améliore le RR moyen mais **dégrade le win rate**. Vérifier l'effet net
  sur l'expectancy en R, pas sur le win rate isolé.
- Ne jamais élargir un stop sur une position perdante. Si le code le permet, c'est un bug
  à traiter en priorité — c'est la mécanique exacte du compte qui explose.

## Circuit breakers à ajouter

`max_consecutive_losses = 3` est un bon réflexe mais mal calibré : avec un win rate de 45 %,
3 pertes d'affilée arrivent ~17 % du temps **par pur hasard**. Le bot s'arrête donc souvent
sans raison statistique. Calibrer sur la distribution empirique (voir `trading-kpi`) :
un seuil à 5–6 est généralement plus juste.

Breakers à ajouter, plus informatifs que le compteur de pertes :

- **Perte journalière** en % d'equity (borne le pire cas quotidien)
- **Drawdown depuis le pic** avec réduction progressive du sizing, plutôt qu'un arrêt binaire
- **Écart live/backtest** : si le win rate live dévie de > 2σ du backtest sur 20 trades,
  suspendre et alerter (voir `live-reconciliation`)
- **Anomalie de marché** : spread, funding extrême, ou vol > 3× la normale ⇒ pas de nouvelle
  entrée

## Checklist avant tout changement de sizing

- [ ] Le stop est fixé avant la taille, jamais l'inverse
- [ ] Stop ≤ 50 % de la distance de liquidation au levier retenu
- [ ] Risque agrégé des positions corrélées vérifié, pas seulement le risque par trade
- [ ] Backtest du nouveau sizing sur un régime bear complet
- [ ] Pire drawdown simulé (Monte Carlo) acceptable psychologiquement et financièrement
- [ ] Comportement testé au cas limite : solde faible, `minNotional`, arrondi de quantité
