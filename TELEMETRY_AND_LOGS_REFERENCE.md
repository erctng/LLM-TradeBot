# Référentiel : Télémétrie, Logs et Calcul des Indicateurs (LLM-TradeBot)

Ce document sert de mémoire centrale pour retrouver facilement l'historique des opérations du robot, interroger ses bases de données et calculer les indicateurs financiers de performance.

---

## 1. Accès aux Logs (VPS)

Le bot principal tourne sur un VPS (IP: `49.13.235.188`) via un conteneur Docker nommé `llm-tradebot`.

### 1.1 Logs Docker (En direct)
Pour voir ce que le robot est en train d'analyser et ses décisions :
```bash
# Connectez-vous au VPS :
ssh -i ~/.ssh/id_ed25519 root@49.13.235.188

# Afficher les 100 dernières lignes de logs :
docker logs llm-tradebot --tail 100

# Suivre les logs en temps réel (Ctrl+C pour quitter) :
docker logs -f llm-tradebot
```

### 1.2 Logs Fichiers (Archivage)
Les logs détaillés de chaque cycle de décision sont sauvegardés dans le dossier persistant du VPS :
- **Chemin VPS** : `/root/LLM-TradeBot/logs/`
- Ces logs contiennent les requêtes exactes envoyées à l'API LLM et le retour de chaque Agent.

---

## 2. Accès aux Données de Trades (Base de données)

Le bot sauvegarde l'ensemble de ses trades et analyses dans une base de données locale SQLite, qui est montée de manière persistante sur le VPS.

- **Chemin VPS** : `/root/LLM-TradeBot/data/analytics/trading.db`

### Tables principales :
- `trades` : Contient l'historique de chaque trade (symbol, action, entry_price, exit_price, pnl, reason, status).
- `agent_logs` : Historique des votes de chaque agent par cycle.

---

## 3. Le Système de Télémétrie (Dashboard)

Pour éviter de manipuler la base de données manuellement, une API de Télémétrie a été mise en place.

### 3.1 Côté Serveur (VPS)
L'API lit directement `trading.db` et calcule les métriques.
- **Fichier** : `src/api/vps_telemetry.py`
- **Port d'écoute** : `8085` (indépendant du port 8000 du bot Docker).
- **Lancement** : L'API peut être lancée en tâche de fond sur le VPS via `nohup python3 /root/LLM-TradeBot/src/api/vps_telemetry.py > telemetry.log 2>&1 &`

### 3.2 Côté Client (Local Windows)
Pour afficher le tableau de bord sur l'ordinateur personnel sans se connecter au VPS :
```powershell
# Depuis le dossier C:\Users\erict\.gemini\antigravity\scratch\LLM-TradeBot
python scripts\local_dashboard.py
```

---

## 4. Calcul des Indicateurs de Performance (KPIs)

Si nous devons recalculer des indicateurs manuellement à partir de `trading.db`, voici les formules mathématiques utilisées par l'API Télémétrie :

1. **Win Rate (Taux de réussite)** :
   `Win Rate = (Nombre de trades gagnants / Nombre total de trades clôturés) * 100`

2. **Avg Win / Avg Loss** :
   - `Avg Win` = Moyenne des PnL > 0
   - `Avg Loss` = Moyenne absolue des PnL < 0

3. **Reward/Risk Ratio (Ratio Gain/Risque)** :
   `R/R Ratio = Avg Win / Avg Loss`

4. **Sharpe Ratio (Ratio de Sharpe)** :
   Mesure la rentabilité ajustée au risque.
   `Sharpe Ratio = (Moyenne des PnL) / (Écart-type des PnL)`
   *(Note : Pour annualiser, on multiplie par la racine carrée du nombre de trades estimés sur une année).*

5. **Max Drawdown (Perte Maximale)** :
   Représente la plus grosse chute du capital depuis son point le plus haut.
   - On calcule le PnL cumulé après chaque trade.
   - `Peak` = Le PnL cumulé maximum atteint à un instant T.
   - `Drawdown` = `(Peak - PnL cumulé actuel) / (Capital + Peak)`
   - `Max Drawdown` = La valeur maximale du Drawdown sur toute la période.

---
*Fichier généré automatiquement pour mémoire.*
