---
name: llm-agent-trading
description: Fiabilité et qualité de la couche de décision multi-agents LLM — sortie structurée, déterminisme, garde-fous anti-hallucination, agrégation de votes, attribution de performance par agent, coût/latence par décision, évaluation de prompts. À charger dès qu'on touche src/agents/, src/llm/, src/trading/multi_agent_trading_bot.py, ou qu'on parle d'"agent", "prompt", "LLM", "consensus", "vote", "reflection", "meta optimizer".
version: "1.0.0"
---

# LLM Multi-Agent Trading Layer

C'est la spécificité de ce bot et sa principale source de risque non financier : un LLM
peut produire une décision confiante, bien formatée et complètement fausse, sans lever
d'erreur.

## Architecture dans ce repo

```
data_sync → features → agents spécialisés (trend, sentiment, quant, regime, risk_audit,
            multi_period, position_analyzer, symbol_selector, trigger_detector)
                                   │ votes
                          decision_core_agent  (agrégation pondérée, SignalWeight)
                                   │
                          MetaLabelingAgent    (P(succès))
                                   │
                          RiskManager          (validate_decision / clamp)
                                   │
                          ExecutionEngine
   reflection_agent + meta_optimizer_agent  ──► ajustement des prompts/poids a posteriori
```

Providers dans `src/llm/` : claude, openai, deepseek, gemini, glm, kimi, minimax, qwen,
openrouter — via `factory.py`. Contrats dans `src/agents/contracts.py`.

## 1. Sortie structurée — jamais de parsing libre

Une décision de trading extraite par regex d'un texte libre est une bombe à retardement.

- Schéma strict (JSON Schema — `jsonschema` est déjà en dépendance) validé **avant** tout
  usage. `RiskManager.validate_format` fait ce travail : il doit rester la seule porte
  d'entrée.
- Tout champ numérique borné dans le schéma : `confidence ∈ [0,100]`, `leverage ∈ [1,5]`,
  `position_size_pct`, `stop_loss_pct` avec min/max plausibles.
- **Rejeter, ne pas réparer.** Une réponse mal formée signale un LLM hors distribution ;
  un parsing tolérant transforme un signal d'alerte en décision. `RiskManager` clampe
  actuellement les valeurs hors bornes — c'est acceptable comme filet, mais **le clamp doit
  être compté et alerté** : un clamp fréquent = prompt à corriger, pas un fonctionnement normal.
- Conserver `llm_raw_output` (déjà fait dans la table `decisions`) : indispensable pour
  post-mortem.

## 2. Déterminisme et reproductibilité

- `temperature=0` pour les agents de décision. La créativité est un défaut ici.
- Journaliser **par décision** : provider, nom exact du modèle, version du prompt, hash du
  contexte d'entrée, tokens in/out, latence.
- Sans version de prompt persistée, `meta_optimizer_agent` qui modifie les prompts rend
  toute analyse historique ininterprétable : on ne sait plus quelle version a produit quels
  résultats. **C'est le point le plus urgent à instrumenter** si ce n'est pas déjà fait.
- Un même contexte doit produire la même décision ; tester cette invariance en CI avec des
  fixtures enregistrées.

## 3. Garde-fous anti-hallucination

- **Aucun chiffre de marché ne vient du LLM.** Prix, ATR, RSI, solde, taille de position
  sont calculés en Python et injectés. Le LLM raisonne, il ne calcule pas.
- Vérifier la cohérence de la décision avec le contexte injecté : si le LLM justifie un
  long par "RSI survendu" alors que le RSI fourni est à 72, rejeter. Ce contrôle de
  cohérence est peu coûteux et attrape des dérives réelles.
- Sanity checks déterministes en aval : SL du bon côté de l'entrée, TP du bon côté,
  RR ≥ minimum, symbole dans l'univers autorisé.
- **Prompt injection** : le sentiment agent consomme du texte externe (news, réseaux
  sociaux). Ce texte est une donnée non fiable, jamais une instruction. L'isoler
  explicitement dans le prompt et ne jamais lui laisser modifier le format de sortie.

## 4. Agrégation des votes

`decision_core_agent` pondère via `SignalWeight` et ajuste par performance
(`adjust_weights_by_performance`). Points de vigilance :

- **Corrélation entre agents** : si trend, multi_period et trigger lisent tous la même EMA,
  leur "consensus" est un seul signal compté trois fois. Mesurer la corrélation des votes
  et dégrouper les agents redondants — c'est le défaut le plus commun des architectures
  multi-agents.
- **Adaptation des poids** : ajuster sur un historique court sur-réagit au bruit. Exiger
  un N minimum par agent, borner l'amplitude d'ajustement, et prévoir un retour aux poids
  par défaut si la performance se dégrade.
- **La dispersion des votes est un signal en soi** : la persister et la donner en feature
  au meta-labeling (voir `meta-labeling`).
- Un agent doit pouvoir répondre "je ne sais pas" (abstention) sans être compté comme
  neutre — ce n'est pas la même information.

## 5. Attribution de performance par agent

Sans ça, impossible de savoir quel agent apporte de la valeur.

À persister par cycle dans `agent_logs` : vote de chaque agent, confiance, latence, coût,
et a posteriori le résultat du trade. Puis calculer par agent :

- taux de réussite quand l'agent est décisif (son vote a fait basculer la décision)
- contribution marginale : performance avec vs sans l'agent, en replay
- corrélation aux autres agents
- coût par décision

**Un agent dont la contribution marginale est nulle ou négative doit être retiré** :
il ajoute du coût, de la latence et du bruit.

## 6. Coût, latence et dégradation

- Budget par cycle : tokens et $ à surveiller. `src/llm/metrics.py` existe — l'exposer dans
  la télémétrie à côté des KPI financiers. Le coût LLM est une charge qui doit apparaître
  dans le PnL net.
- **Chemin dégradé obligatoire** : si un provider est indisponible, en rate limit ou trop
  lent, le bot doit soit basculer sur un fallback, soit **ne pas trader**. Il ne doit jamais
  trader sur un consensus partiel silencieux.
- Timeout par agent, et un timeout global de cycle. Une décision arrivée trop tard doit
  être invalidée (voir `execution-quality` §5).
- Cache sur les appels dont le contexte n'a pas changé.

## 7. Évaluation des prompts

`meta_optimizer_agent` modifie les prompts dynamiquement : c'est puissant et dangereux.
Aucun changement de prompt ne doit atteindre la production sans :

- un **jeu d'évaluation figé** de contextes de marché historiques avec l'issue connue
- comparaison A/B sur ce jeu : accuracy directionnelle, calibration de la confiance, taux
  de format invalide, coût
- validation en shadow mode (décisions enregistrées, non exécutées) avant activation
- versionnage et rollback possible en une commande

Un prompt optimisé sur l'historique récent est sujet exactement au même overfitting qu'un
paramètre de stratégie — les règles de `backtest-integrity` s'appliquent intégralement.

## Checklist

- [ ] Schéma strict validé avant usage, rejets et clamps comptés et alertés
- [ ] `temperature=0`, provider/modèle/version de prompt journalisés par décision
- [ ] Aucun calcul numérique délégué au LLM
- [ ] Texte externe traité comme donnée, jamais comme instruction
- [ ] Votes individuels + dispersion persistés
- [ ] Chemin dégradé testé (provider down ⇒ pas de trade)
- [ ] Tout changement de prompt évalué en shadow avant activation
