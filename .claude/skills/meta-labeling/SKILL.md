---
name: meta-labeling
description: Couche ML de filtrage des signaux (meta-labeling López de Prado) — triple barrier, sample weights, purged CV, calibration des probabilités, choix du seuil par espérance de gain, sizing par confiance. À charger dès qu'on touche src/agents/meta/meta_labeling_agent.py, src/agents/ai_prediction_filter_agent.py, ou qu'on parle de "meta labeling", "filtre ML", "probabilité de succès", "LightGBM", "réentraînement".
version: "1.0.0"
---

# Meta-Labeling

Le meta-labeling ne génère pas de signaux : il décide **si** un signal primaire mérite
d'être joué et **avec quelle taille**. C'est la façon la plus fiable d'améliorer un bot
qui a déjà un edge faible mais réel.

## État dans ce repo

`src/agents/meta/meta_labeling_agent.py` : `MetaLabelingAgent` avec `train()`,
`predict_success_probability()`, modèle persisté dans `data/models/meta_labeling`.
Le modèle primaire est le consensus multi-agents (`decision_core_agent`).

Architecture correcte :

```
Agents LLM + DecisionCore  ──►  signal primaire (direction)
                                       │
MetaLabelingAgent          ──►  P(succès)  ──►  trade / skip / taille
```

Le modèle méta ne prédit **jamais la direction** — uniquement si le signal primaire va
réussir. C'est un classifieur binaire. S'il prédit aussi la direction, l'architecture est
cassée et le gain de meta-labeling disparaît.

## 1. Labeling — triple barrier

Un label binaire "profitable ou non" basé sur un horizon fixe est faible : il ignore le
chemin. La méthode correcte pose trois barrières depuis l'entrée :

```
barrière haute  = entry + tp_mult * ATR      → label 1
barrière basse  = entry - sl_mult * ATR      → label 0
barrière temps  = entry_ts + horizon         → label = signe du PnL à l'expiration
```

Le premier touché gagne. Les barrières doivent correspondre **aux SL/TP réellement
utilisés** par le bot, sinon le modèle apprend une tâche différente de celle qu'il filtre.
Le repo utilise des Chandelier exits : le labeling doit simuler ce trailing, pas un TP fixe.

## 2. Sample weights — non optionnel

Les labels de trading se chevauchent : deux trades ouverts simultanément partagent les
mêmes rendements futurs. Ils ne sont pas i.i.d., ce qui fausse l'entraînement et toute
validation croisée.

- **Uniqueness weight** : poids inversement proportionnel au nombre de labels concurrents
  sur la même période
- **Return attribution** : pondérer par |rendement| — un trade à +3 % est plus informatif
  qu'un à +0.05 %
- **Time decay** : les régimes crypto changent ; pondérer les échantillons récents plus fort

## 3. Validation — purged K-fold + embargo

Un K-fold naïf sur des labels chevauchants **fuit** systématiquement : le Sharpe validé est
fantaisiste. Obligatoire :

- **Purge** : retirer du train tout échantillon dont l'horizon `[t, t+h]` recouvre le fold
  de test
- **Embargo** : exclure en plus une bande de ~1 % de la série après le fold de test
  (autocorrélation résiduelle)
- Jamais de `shuffle=True` sur des séries temporelles

## 4. Features du modèle méta

Le méta-modèle voit ce que le modèle primaire ne voit pas. Combinaison :

- **Contexte de marché** : volatilité, régime, funding, spread, heure UTC, volume relatif
- **Caractéristiques du signal** : confiance du consensus, dispersion des votes d'agents,
  alignement multi-timeframe, agent déclencheur
- **État du bot** : pertes consécutives, drawdown courant, exposition, temps depuis le
  dernier trade
- **Cohérence historique** : performance récente de ce type de signal sur ce symbole

Le désaccord entre agents est une feature à haute valeur et gratuite ici : un consensus
unanime et un vote 4-3 ont des taux de réussite très différents. Le persister dans
`decisions` s'il ne l'est pas déjà.

## 5. Calibration — indispensable

Un LightGBM sort des scores, pas des probabilités. `predict_proba = 0.7` ne signifie pas
70 % de réussite. Sans calibration, tout seuil et tout sizing par confiance sont arbitraires.

- Calibration isotonique ou Platt sur un **set de calibration séparé** (pas le train)
- Vérifier avec un **diagramme de fiabilité** et le Brier score
- Recalibrer à chaque réentraînement — la calibration dérive plus vite que le modèle

## 6. Seuil — par espérance, pas par accuracy

L'accuracy est le mauvais critère : les classes sont déséquilibrées et les coûts asymétriques.

```
EV(p) = p * avg_win - (1-p) * avg_loss - coûts
seuil* = p tel que EV(p) = 0, plus une marge de sécurité
```

Balayer le seuil et tracer PnL OOS, nombre de trades et Sharpe. Un seuil trop haut ne
laisse plus assez de trades pour que le résultat soit significatif. Rapporter les deux.

**Sizing par confiance** (souvent supérieur au filtre binaire) : au lieu de skip/trade,
moduler la taille par `p` — par exemple `size ∝ (p - seuil)/(1 - seuil)`, clampé.

## 7. Réentraînement

- Volume minimum : **≥ 300 trades étiquetés**, idéalement 1000. En dessous, le modèle
  mémorise. `MetaLabelingAgent.train()` doit refuser sous un seuil plancher plutôt que
  produire un modèle inutilisable.
- Cadence : mensuelle ou déclenchée par dérive, pas à chaque trade
- **Toujours conserver le modèle précédent** et comparer sur un hold-out commun avant de
  promouvoir. Un réentraînement automatique sans gate de qualité peut dégrader le bot
  silencieusement.
- Versionner le modèle avec sa période de train, ses features et ses métriques OOS

## 8. Détection de dérive

- **PSI** sur la distribution des features (> 0.25 = dérive sérieuse)
- Écart entre probabilité prédite moyenne et taux de réussite réalisé, en fenêtre glissante
- Effondrement de l'AUC OOS ⇒ suspendre le filtre et revenir au signal primaire nu

## Pièges

- Entraîner sur les trades **exécutés seulement** : biais de sélection majeur, le modèle ne
  voit jamais les signaux filtrés. Étiqueter **tous les signaux générés**, exécutés ou non.
- Utiliser le PnL réalisé comme label alors qu'il dépend du sizing : labelliser sur le
  **rendement du trade**, pas sur le montant.
- Oublier les frais dans le label : un trade à +0.03 % est un perdant net.
