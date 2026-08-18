from dataclasses import dataclass

@dataclass
class SignalWeight:
    """信号权重配置
    
    注意: 所有权重应该合计为 1.0 (不包括动态 sentiment)
    优化后配置 (2026-01-07): 基于回测分析进一步优化
    - 增加1h权重，减少短周期噪音
    - 减少prophet权重，更依赖技术指标
    """
    # 趋势信号 (合计 0.48) - 增加长周期权重
    trend_5m: float = 0.03   # 减少5m噪音影响
    trend_15m: float = 0.13  # 略增
    trend_1h: float = 0.32   # 增加1h权重 (核心趋势判断)
    # 震荡信号 (合计 0.21)
    oscillator_5m: float = 0.03  # 减少5m噪音
    oscillator_15m: float = 0.07
    oscillator_1h: float = 0.11  # 增加1h权重
    # Prophet ML 预测权重 - 进一步减少
    prophet: float = 0.06  # 减少ML权重，避免过拟合
    # 情绪信号 (动态权重)
    sentiment: float = 0.25
    # 其他扩展信号（如LLM）
    llm_signal: float = 0.0  # 未接入 DecisionCore：LLM 影响力经 StrategyEngine 上游生效

    def total_weight(self) -> float:
        """所有信号权重之和。应为 1.0，否则加权分数无法达到名义上限。"""
        return round(
            self.trend_5m + self.trend_15m + self.trend_1h
            + self.oscillator_5m + self.oscillator_15m + self.oscillator_1h
            + self.prophet + self.sentiment + self.llm_signal,
            10
        )
