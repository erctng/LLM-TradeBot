---
name: execution-quality
description: Qualité d'exécution sur Binance Futures — slippage réel, maker/taker, filtres d'échange (tickSize, stepSize, minNotional), types d'ordres, reduceOnly, idempotence, gestion des erreurs API, funding, latence. À charger dès qu'on touche src/execution/engine.py, src/api/binance_client.py, src/exchanges/, ou qu'on parle d'"ordre", "exécution", "slippage", "fill", "API Binance", "ordre rejeté".
version: "1.0.0"
---

# Execution Quality

L'écart entre décision et exécution est un coût invisible qui érode l'edge en continu.
Sur des cycles courts à levier, il dépasse souvent la marge du signal.

## Périmètre

`src/execution/engine.py`, `src/api/binance_client.py`, `src/api/binance_websocket.py`,
`src/exchanges/`. Résultats persistés dans la table `executions`
(`entry_price`, `quantity`, `orders_data`, `success`, `message`).

## 1. Filtres d'échange — cause n°1 des ordres rejetés

Toute quantité et tout prix doivent être conformés **avant** l'envoi, via les filtres de
`exchangeInfo` du symbole (ils changent, ne jamais les coder en dur) :

| Filtre | Contrainte | Conformation |
|---|---|---|
| `PRICE_FILTER.tickSize` | prix multiple de tickSize | arrondi **vers le marché défavorable** |
| `LOT_SIZE.stepSize` | qty multiple de stepSize | **toujours arrondir vers le bas** |
| `MIN_NOTIONAL` | qty*prix ≥ min | sinon ne pas envoyer et logger |
| `MARKET_LOT_SIZE` | max qty ordre market | découper |
| `PERCENT_PRICE` | prix limite dans une bande | rejet sinon |

Arrondir la quantité vers le haut provoque un `-2019 Margin is insufficient` ou dépasse le
risque calculé. Toujours `floor` sur `stepSize`, avec `Decimal` — jamais de float :
`0.1 + 0.2 != 0.3` fait rejeter des ordres. `src/backtest/precision.py` existe déjà pour
cette logique côté backtest ; le live doit utiliser la même.

## 2. Codes d'erreur Binance à traiter explicitement

| Code | Sens | Traitement |
|---|---|---|
| `-1021` | timestamp hors fenêtre | resynchroniser l'offset serveur, retry |
| `-1013` | filtre non respecté | bug de conformation, **ne pas retry** |
| `-2019` | marge insuffisante | réduire ou annuler, alerter |
| `-2011` | ordre inconnu à l'annulation | déjà exécuté/annulé, traiter comme succès |
| `-4164` | notionnel < minimum | ne pas retry |
| `-1003` | rate limit | backoff exponentiel obligatoire |
| `-4131` | protection PERCENT_PRICE | marché illiquide, passer |

Distinguer **erreurs retryables** (réseau, 5xx, `-1021`, `-1003`) des **erreurs
définitives** (filtres, marge). Retry aveugle sur une erreur de filtre = boucle infinie ;
retry aveugle sur un ordre market = **double position**.

## 3. Idempotence — le risque le plus grave

Un timeout réseau ne signifie pas que l'ordre a échoué : il peut avoir été accepté.
Rejouer aveuglément ouvre une position en double, hors de tout contrôle de risque.

Règles :

- `newClientOrderId` déterministe et unique par intention de trade
- Après timeout : **d'abord interroger** l'état (`GET /fapi/v1/order` par `origClientOrderId`),
  jamais renvoyer directement
- Réconcilier la position réelle (`positionRisk`) avec l'état interne à chaque cycle, et
  au redémarrage. L'exchange est la source de vérité, pas la mémoire du bot.
- SL/TP en `reduceOnly=true` : sinon un stop peut **ouvrir** une position inverse
- Après une clôture, annuler les ordres SL/TP orphelins — sinon ils flottent et se
  déclenchent sur un trade ultérieur

## 4. Mesurer le slippage réel

Le backtest suppose 10 bps. Le live doit mesurer :

```
slippage_bps = (fill_price - decision_price)/decision_price * 10000 * sens
```

Persister `decision_price`, `fill_price`, `decision_ts`, `fill_ts` dans `executions` puis
suivre : slippage médian et p95 par symbole, par taille, par régime de volatilité. Si le
p95 live dépasse l'hypothèse de backtest, **le backtest est invalide** — le corriger avant
toute autre optimisation.

## 5. Latence — spécificité de ce bot

Un cycle multi-agents LLM prend des secondes à des minutes. Le prix au moment de la
décision n'est plus le prix à l'exécution.

- Logger `decision_ts` et `fill_ts`, suivre la distribution du delta
- **Invalider une décision périmée** : si `now - decision_ts > seuil` ou si le prix a bougé
  de plus de `k*ATR` depuis, annuler plutôt qu'exécuter. Une décision LLM basée sur un
  contexte de marché obsolète est pire qu'aucune décision.
- Modéliser cette latence dans le backtest (voir `backtest-integrity`)

## 6. Maker vs taker

Taker = 0.04 %, maker = 0.02 %. Sur 200 trades A/R par mois, l'écart représente plusieurs
pourcents d'equity par an — souvent plus que l'écart entre deux versions de stratégie.

Post-only en entrée quand le signal n'est pas urgent, avec repli en market après timeout.
**Ne jamais** utiliser post-only sur un stop loss : la protection doit s'exécuter.

## 7. Funding

Réglé toutes les 8 h (00/08/16 UTC). Un long paie quand le funding est positif.
- Éviter d'ouvrir juste avant un settlement quand le funding est extrême et défavorable
- Un funding > 0.1 % par période signale un marché surchauffé — souvent un signal
  contrarian exploitable en soi
- Le funding cumulé doit apparaître dans le PnL du trade, sinon les KPI sont faux

## Checklist avant tout code d'exécution

- [ ] Quantité/prix conformés aux filtres via `Decimal`, arrondi vers le bas sur la qty
- [ ] `newClientOrderId` déterministe, vérification d'état après timeout
- [ ] SL/TP en `reduceOnly`, orphelins annulés à la clôture
- [ ] Erreurs retryables et définitives distinguées, backoff sur `-1003`
- [ ] Réconciliation position exchange ↔ état interne au démarrage et par cycle
- [ ] Slippage et latence mesurés et persistés
- [ ] Testé d'abord sur **testnet Binance**, jamais directement en production
