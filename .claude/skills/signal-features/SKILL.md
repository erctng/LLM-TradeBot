---
name: signal-features
description: Construction correcte des indicateurs et features de marché — repainting, alignement multi-timeframe, stationnarité, fuite temporelle, détection de régime, microstructure (funding, OI, order book). À charger dès qu'on touche src/features/, src/models/, src/agents/regime_detector_agent.py, multi_period_agent.py, quant_analyst_agent.py, ou qu'on parle d'"indicateur", "feature", "RSI/ATR/EMA", "timeframe", "régime de marché".
version: "1.0.0"
---

# Signal & Features

Les features sont l'entrée de tout le reste : agents LLM, meta-labeling, decision core.
Une feature qui fuit rend tout l'aval invalide sans jamais lever d'erreur.

## Périmètre

`src/features/builder.py`, `src/features/technical_features.py`, `src/models/prophet_model.py`,
et les agents consommateurs (`quant_analyst_agent`, `multi_period_agent`,
`regime_detector_agent`, `trigger_detector_agent`).

## 1. Repainting — la bougie en cours

Un indicateur calculé sur une bougie **non close** change jusqu'à la clôture. Un backtest
qui lit `df.iloc[-1]` sur des bougies closes voit une valeur définitive ; le live voit une
valeur qui bouge. Les deux ne décident pas sur la même donnée.

Règle : **toute décision se prend sur `df.iloc[-2]` si `df.iloc[-1]` est la bougie en
cours**, ou le dernier index est exclu du DataFrame en amont. Choisir une convention,
la documenter, et la vérifier identique en backtest et en live.

Indicateurs particulièrement piégeux : tout ce qui utilise un centrage
(`center=True` dans un rolling regarde le futur), les zigzags, les pivots confirmés
a posteriori, les niveaux de support/résistance recalculés sur toute la série.

## 2. Multi-timeframe — l'erreur d'alignement

`multi_period_agent` combine plusieurs timeframes. Piège : à `t = 10h17`, la bougie H4 en
cours (08h–12h) **n'est pas close**. L'utiliser injecte de l'information partielle du futur
par rapport à ce qu'un observateur en 08h aurait su.

Règle : n'utiliser d'un timeframe supérieur que les bougies **closes à `t`**. Le resampling
pandas doit être `label='right', closed='right'` puis décalé, sinon l'agrégat porte le
timestamp de son début tout en contenant sa fin.

## 3. Stationnarité

Les prix bruts sont non stationnaires. Un modèle entraîné sur BTC à 30k ne généralise pas
à 90k. Toute feature nourrissant un modèle ML doit être **relative** :

- rendements log plutôt que prix
- distance à une MA **en unités d'ATR**, pas en dollars
- rank percentile de la feature sur une fenêtre glissante
- volume relatif à sa moyenne mobile, pas en absolu

L'exception : le fractional differencing préserve de la mémoire tout en atteignant la
stationnarité. Utile si un modèle a besoin de la notion de niveau.

## 4. Fuite dans le prétraitement

- Scaler/normaliser **fitté sur le train uniquement**, appliqué au test
- Imputation de NaN : `fillna(method='bfill')` est une fuite directe du futur. Utiliser
  `ffill` uniquement.
- Aucune statistique globale (mean, std, min/max sur toute la série) dans une feature
- Le warm-up des indicateurs (les 200 premières bougies d'une EMA200) doit être exclu, pas
  rempli

## 5. Détection de régime

Le facteur d'amélioration le plus élevé de ce bot : la plupart des stratégies ont un edge
dans un régime et un anti-edge dans l'autre. Mesurer les KPI **par régime** avant de
chercher à améliorer la stratégie globale.

Axes de classification utiles, dans l'ordre :

1. **Volatilité** : ATR% ou vol réalisée vs son percentile historique (bas / normal / stress)
2. **Tendance vs range** : ADX, pente d'EMA, ou ratio d'efficience de Kaufman
   (`|net| / somme|moves|`) — le plus lisible
3. **Structure de marché** : succession de higher-highs / lower-lows
4. **Sentiment dérivés** : funding rate, open interest, ratio long/short

`decision_core_agent` a déjà un `_evaluate_choppy_strategy` : c'est le bon axe, l'étendre
en classification explicite persistée par cycle permet ensuite la décomposition des KPI.

**Persister le régime dans la table `decisions`** : sans ça, l'analyse par régime est
impossible a posteriori.

## 6. Microstructure crypto — features sous-exploitées

Signaux propres au crypto, indisponibles en actions et peu utilisés ici :

- **Funding rate** et sa dérivée : extrêmes = positionnement saturé, contrarian
- **Open interest** vs prix : OI↑ + prix↑ = nouvelle conviction ; OI↓ + prix↑ = short squeeze
  (moins durable)
- **Liquidations cumulées** : les cascades marquent souvent des retournements locaux
- **Basis perp/spot** : mesure directe du levier du marché
- **Déséquilibre du carnet** et taker buy/sell ratio (déjà accessible via l'API klines :
  `taker_buy_base_volume`)

## 7. Multiplicité des tests

Tester 50 indicateurs et garder le meilleur produit un faux positif quasi garanti.
Corriger le seuil de significativité (Bonferroni ou FDR) ou, plus simple : exiger qu'une
feature ait une **justification économique** avant d'être testée. Une feature retenue sur
la seule base d'un backtest est du data mining.

## Checklist feature

- [ ] Calculable en temps réel avec la seule information disponible à `t`
- [ ] Aucune bougie en cours utilisée (ou convention documentée et identique live/backtest)
- [ ] Stationnaire ou relative
- [ ] Prétraitement fitté sur le train seul
- [ ] Corrélation aux features existantes < 0.9 (sinon redondance)
- [ ] Hypothèse économique écrite **avant** le test
- [ ] Testée sur ≥ 2 symboles et ≥ 2 régimes
