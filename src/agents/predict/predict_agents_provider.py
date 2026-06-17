import pandas as pd

from typing import List, Dict, Optional

from src.agents.predict import PredictAgent
from src.agents.predict import PredictResult
from src.features.technical_features import TechnicalFeatureEngineer
from src.models.prophet_model import ProphetAutoTrainer
from src.utils.logger import log
from src.agents.agent_config import AgentConfig
from src.api.binance_client import BinanceClient
from src.server.state import global_state

class PredictAgentsProvider:
    def __init__(
        self,
        client: BinanceClient,
        agents_config: AgentConfig,
        symbols: List[str]
    ):
        self.client = client
        self.agent_config = agents_config
        print("[DEBUG] Creating TechnicalFeatureEngineer...")
        self.feature_engineer = TechnicalFeatureEngineer()  # 🔮 特征工程器 for Prophet
        print("[DEBUG] TechnicalFeatureEngineer created")
        self.predict_agents = {}

        self.auto_trainer = ProphetAutoTrainer(
            binance_client=client,
            interval_hours=2.0,  # 每 2 小时训练一次
            training_days=70,    # 使用最近 70 天数据 (10x samples)
        )

        for symbol in symbols:
            print(f"[DEBUG] Creating PredictAgent for {symbol}...")
            self.predict_agents[symbol] = PredictAgent(horizon='30m', symbol=symbol)
            print(f"[DEBUG] PredictAgent for {symbol} created")

    def add_agent_for_symbol(self, symbol: str, horizon='30m'):
        if self.agent_config.predict_agent and symbol not in self.predict_agents:
            self.predict_agents[symbol] = PredictAgent(horizon, symbol=symbol)
            log.info(f"🆕 Added PredictAgent for new symbol: {symbol}")

    def reload(self, symbols: List[str], horizon='30m'):
        for symbol in symbols:
            self.add_agent_for_symbol(symbol, horizon=horizon)

    def start_auto_trainer(
        self,
        symbols: List[str]
    ):
        if not symbols:
            return
            
        # Ensure all symbols are initialized in predict_agents
        for symbol in symbols:
            if symbol not in self.predict_agents:
                self.predict_agents[symbol] = PredictAgent(horizon='30m', symbol=symbol)
                log.info(f"🆕 Initialized PredictAgent for {symbol} (auto-trainer fallback)")
                
        if self.predict_agents:
            self.auto_trainer.start(
                self.predict_agents,
                symbols)

    async def predict(
        self,
        symbol: str,
        processed_dfs: Dict[str, "pd.DataFrame"]
    ) -> Optional[PredictResult]:
        if self.agent_config.predict_agent and symbol in self.predict_agents:
            df_15m_features = self.feature_engineer.build_features(processed_dfs['15m'])
            latest_features = {}
            if not df_15m_features.empty:
                latest = df_15m_features.iloc[-1].to_dict()
                latest_features = {
                    k: v for k, v in latest.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                }

            res = await self.predict_agents[symbol].predict(latest_features)
            global_state.prophet_probability = res.probability_up
            p_up_pct = res.probability_up * 100
            direction = "↗UP" if res.probability_up > 0.55 else ("↘DN" if res.probability_up < 0.45 else "➖NEU")
            predict_msg = f"Probability Up: {p_up_pct:.1f}% {direction} (Conf: {res.confidence*100:.0f}%)"
            global_state.add_agent_message("predict_agent", predict_msg, level="info")
            return res
        return None
