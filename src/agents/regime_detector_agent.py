"""
市场状态检测器 (Regime Detector)
识别当前市场处于趋势/震荡/高波动状态
"""

import pandas as pd
import numpy as np
from typing import Dict
from enum import Enum
from ta.trend import ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange


class MarketRegime(Enum):
    """市场状态分类"""
    TRENDING_UP = "trending_up"       # 明确上涨趋势
    TRENDING_DOWN = "trending_down"   # 明确下跌趋势
    CHOPPY = "choppy"                 # 震荡市（垃圾时间）
    VOLATILE = "volatile"             # 高波动（危险）
    VOLATILE_DIRECTIONLESS = "volatile_directionless"  # 🆕 ADX高但方向不明（洗盘）
    UNKNOWN = "unknown"               # 无法判断


# 经验马尔可夫转移矩阵 (Empirical Markov Transition Matrix)
# 表示从当前状态转移到下一个状态的先验概率
MARKOV_TRANSITION_MATRIX = {
    MarketRegime.TRENDING_UP: {
        MarketRegime.TRENDING_UP: 0.60,
        MarketRegime.CHOPPY: 0.25,
        MarketRegime.VOLATILE: 0.10,
        MarketRegime.TRENDING_DOWN: 0.05,
        MarketRegime.VOLATILE_DIRECTIONLESS: 0.0,
        MarketRegime.UNKNOWN: 0.0
    },
    MarketRegime.TRENDING_DOWN: {
        MarketRegime.TRENDING_DOWN: 0.60,
        MarketRegime.CHOPPY: 0.25,
        MarketRegime.VOLATILE: 0.10,
        MarketRegime.TRENDING_UP: 0.05,
        MarketRegime.VOLATILE_DIRECTIONLESS: 0.0,
        MarketRegime.UNKNOWN: 0.0
    },
    MarketRegime.CHOPPY: {
        MarketRegime.CHOPPY: 0.70,
        MarketRegime.TRENDING_UP: 0.10,
        MarketRegime.TRENDING_DOWN: 0.10,
        MarketRegime.VOLATILE: 0.05,
        MarketRegime.VOLATILE_DIRECTIONLESS: 0.05,
        MarketRegime.UNKNOWN: 0.0
    },
    MarketRegime.VOLATILE: {
        MarketRegime.VOLATILE: 0.40,
        MarketRegime.VOLATILE_DIRECTIONLESS: 0.30,
        MarketRegime.CHOPPY: 0.20,
        MarketRegime.TRENDING_UP: 0.05,
        MarketRegime.TRENDING_DOWN: 0.05,
        MarketRegime.UNKNOWN: 0.0
    },
    MarketRegime.VOLATILE_DIRECTIONLESS: {
        MarketRegime.VOLATILE_DIRECTIONLESS: 0.50,
        MarketRegime.CHOPPY: 0.30,
        MarketRegime.VOLATILE: 0.20,
        MarketRegime.TRENDING_UP: 0.0,
        MarketRegime.TRENDING_DOWN: 0.0,
        MarketRegime.UNKNOWN: 0.0
    },
    MarketRegime.UNKNOWN: {
        MarketRegime.UNKNOWN: 1.0,
        MarketRegime.TRENDING_UP: 0.0,
        MarketRegime.TRENDING_DOWN: 0.0,
        MarketRegime.CHOPPY: 0.0,
        MarketRegime.VOLATILE: 0.0,
        MarketRegime.VOLATILE_DIRECTIONLESS: 0.0
    }
}


class RegimeDetector:
    """
    市场状态检测器
    
    核心功能：
    1. 使用 ADX 判断趋势强度
    2. 使用布林带宽度判断波动性
    3. 使用 ATR 判断风险水平
    4. 综合判断市场状态
    
    决策规则：
    - CHOPPY（震荡市）：禁止追涨杀跌，只做区间交易
    - VOLATILE（高波动）：禁止开仓或降低杠杆
    - UNKNOWN（无法判断）：强制观望
    """
    
    def __init__(self,
                 adx_trend_threshold: float = 25.0,    # ADX > 25 为趋势
                 adx_choppy_threshold: float = 20.0,   # ADX < 20 为震荡
                 bb_width_volatile_ratio: float = 1.5,  # 布林带宽度 > 均值1.5倍为高波动
                 atr_high_threshold: float = 2.0):      # ATR% > 2% 为高波动
        """
        初始化市场状态检测器
        
        Args:
            adx_trend_threshold: ADX 趋势阈值
            adx_choppy_threshold: ADX 震荡阈值
            bb_width_volatile_ratio: 布林带宽度波动比率
            atr_high_threshold: ATR 高波动阈值（百分比）
        """
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_choppy_threshold = adx_choppy_threshold
        self.bb_width_volatile_ratio = bb_width_volatile_ratio
        self.atr_high_threshold = atr_high_threshold
    
    def detect_regime(self, df: pd.DataFrame) -> Dict:
        """
        检测市场状态
        
        Args:
            df: K线数据（必须包含技术指标）
            
        Returns:
            {
                'regime': MarketRegime,
                'confidence': float,  # 0-100
                'adx': float,
                'bb_width_pct': float,
                'atr_pct': float,
                'trend_direction': str,  # 'up', 'down', 'neutral'
                'reason': str
            }
        """
        
        # 1. 计算 ADX（如果没有则计算）
        adx = self._get_or_calculate_adx(df)
        
        # 2. 计算布林带宽度百分比
        bb_width_pct = self._calculate_bb_width_pct(df)
        
        # 3. 计算 ATR 百分比
        atr_pct = self._calculate_atr_pct(df)
        
        # 4. 判断趋势方向
        trend_direction = self._detect_trend_direction(df)
        
        # 5. 综合判断市场状态
        regime, confidence, reason = self._classify_regime(
            adx, bb_width_pct, atr_pct, trend_direction, df
        )
        
        # ✅ Sanity Checks: Clip values to valid ranges and handle NaN
        def safe_clip(val, min_val, max_val, default=0.0):
            """Clip value to range, handle NaN/None/inf"""
            if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
                return default
            return max(min_val, min(max_val, float(val)))
        
        confidence = safe_clip(confidence, 0, 100, 50.0)
        adx = safe_clip(adx, 0, 100, 20.0)
        bb_width_pct = safe_clip(bb_width_pct, 0, 50, 2.0)
        atr_pct = safe_clip(atr_pct, 0, 20, 0.5)
        
        # 6. CHOPPY 专项分析 (Range Trading Intelligence)
        choppy_analysis = None
        if regime == MarketRegime.CHOPPY:
            choppy_analysis = self._analyze_choppy_market(df, bb_width_pct)
        
        # 7. Markov Chain Probabilities for next regime
        markov_probs = {k.value: v for k, v in MARKOV_TRANSITION_MATRIX.get(regime, MARKOV_TRANSITION_MATRIX[MarketRegime.UNKNOWN]).items()}
        
        return {
            'regime': regime.value,
            'confidence': confidence,
            'adx': adx,
            'bb_width_pct': bb_width_pct,
            'atr_pct': atr_pct,
            'trend_direction': trend_direction,
            'reason': reason,
            'position': self._calculate_price_position(df),
            'choppy_analysis': choppy_analysis,  # 🆕 CHOPPY-specific insights
            'markov_probabilities': markov_probs # 🆕 Markov chain predictions
        }
    
    def _get_or_calculate_adx(self, df: pd.DataFrame) -> float:
        """
        获取或计算 ADX
        
        ADX (Average Directional Index) 用于衡量趋势强度
        - ADX > 25: 强趋势
        - ADX < 20: 弱趋势/震荡
        """
        # 如果已有 ADX 列，直接使用
        if 'adx' in df.columns:
            return df['adx'].iloc[-1]

        # 从原始 OHLC 计算 ADX（兼容回测原始K线）
        if {'high', 'low', 'close'}.issubset(df.columns) and len(df) >= 20:
            try:
                tail = df[['high', 'low', 'close']].tail(200)
                adx = ADXIndicator(
                    high=tail['high'],
                    low=tail['low'],
                    close=tail['close'],
                    window=14
                ).adx().iloc[-1]
                return float(adx)
            except Exception:
                pass
        
        # 否则简化计算（使用 EMA 差值作为替代）
        if 'close' in df.columns and len(df) >= 26:
            close = df['close']
            ema12 = close.ewm(span=12, adjust=False).mean().iloc[-1]
            ema26 = close.ewm(span=26, adjust=False).mean().iloc[-1]
            ema_diff = abs(ema12 - ema26)
            price = close.iloc[-1]
            adx_proxy = (ema_diff / price) * 100 * 10  # 转换为类似 ADX 的值
            return adx_proxy
        
        # 无法计算，返回中性值
        return 20.0
    
    def _calculate_bb_width_pct(self, df: pd.DataFrame) -> float:
        """
        计算布林带宽度百分比
        
        宽度 = (上轨 - 下轨) / 中轨 * 100
        """
        if 'bb_upper' in df.columns and 'bb_lower' in df.columns and 'bb_middle' in df.columns:
            upper = df['bb_upper'].iloc[-1]
            lower = df['bb_lower'].iloc[-1]
            middle = df['bb_middle'].iloc[-1]
            
            if middle > 0:
                width_pct = ((upper - lower) / middle) * 100
                return width_pct

        # 从原始价格计算布林带
        if 'close' in df.columns and len(df) >= 20:
            try:
                close = df['close'].tail(200)
                bb = BollingerBands(close=close, window=20, window_dev=2)
                upper = bb.bollinger_hband().iloc[-1]
                lower = bb.bollinger_lband().iloc[-1]
                middle = bb.bollinger_mavg().iloc[-1]
                if middle > 0:
                    return ((upper - lower) / middle) * 100
            except Exception:
                pass
        
        # 无法计算，返回默认值
        return 2.0
    
    def _calculate_atr_pct(self, df: pd.DataFrame) -> float:
        """
        计算 ATR 百分比
        
        ATR% = ATR / 当前价格 * 100
        """
        if 'atr' in df.columns:
            atr = df['atr'].iloc[-1]
            price = df['close'].iloc[-1]
            
            if price > 0:
                atr_pct = (atr / price) * 100
                return atr_pct

        # 从原始 OHLC 计算 ATR
        if {'high', 'low', 'close'}.issubset(df.columns) and len(df) >= 20:
            try:
                tail = df[['high', 'low', 'close']].tail(200)
                atr = AverageTrueRange(
                    high=tail['high'],
                    low=tail['low'],
                    close=tail['close'],
                    window=14
                ).average_true_range().iloc[-1]
                price = tail['close'].iloc[-1]
                if price > 0:
                    return (float(atr) / price) * 100
            except Exception:
                pass
        
        # 无法计算，返回默认值
        return 0.5
    
    def _detect_trend_direction(self, df: pd.DataFrame) -> str:
        """
        检测趋势方向
        
        使用 SMA20 和 SMA50 判断
        """
        if 'sma_20' in df.columns and 'sma_50' in df.columns:
            sma20 = df['sma_20'].iloc[-1]
            sma50 = df['sma_50'].iloc[-1]
            price = df['close'].iloc[-1]
            
            # 价格和均线关系
            if price > sma20 > sma50:
                return 'up'
            elif price < sma20 < sma50:
                return 'down'

        # 从原始收盘价计算 SMA
        if 'close' in df.columns and len(df) >= 50:
            close = df['close'].tail(200)
            sma20 = close.rolling(window=20).mean().iloc[-1]
            sma50 = close.rolling(window=50).mean().iloc[-1]
            price = close.iloc[-1]
            if price > sma20 > sma50:
                return 'up'
            if price < sma20 < sma50:
                return 'down'
        
        return 'neutral'
    
    def _classify_regime(self, 
                        adx: float,
                        bb_width_pct: float,
                        atr_pct: float,
                        trend_direction: str,
                        df: pd.DataFrame = None) -> tuple:
        """
        综合分类市场状态 (Enhanced with TSS)
        
        Returns:
            (regime, confidence, reason)
        """
        
        # 1. 高波动检测（最高优先级）
        if atr_pct > self.atr_high_threshold:
            return (
                MarketRegime.VOLATILE,
                80.0,
                f"高波动市场（ATR {atr_pct:.2f}% > {self.atr_high_threshold}%）"
            )

        # 2. Calculate Trend Strength Score (TSS)
        # TSS Components:
        # - ADX (0-100): Weight 40%
        # - EMA Alignment (Boolean): Weight 30%
        # - MACD Pulse (Boolean): Weight 30%
        
        tss = 0
        tss_details = []
        
        # Component A: ADX
        if adx > 25:
            tss += 40
            tss_details.append("ADX>25(+40)")
        elif adx > 20:
            tss += 20
            tss_details.append("ADX>20(+20)")
            
        # Component B: EMA Alignment
        if trend_direction in ['up', 'down']:
            tss += 30
            tss_details.append("EMA_Aligned(+30)")
            
        # Component C: MACD Momentum (if available)
        macd_aligned = False
        if df is not None and 'macd' in df.columns and 'macd_signal' in df.columns:
            macd = df['macd'].iloc[-1]
            signal = df['macd_signal'].iloc[-1]
            if (trend_direction == 'up' and macd > signal > 0) or \
               (trend_direction == 'down' and macd < signal < 0):
                tss += 30
                tss_details.append("MACD_Momentum(+30)")
                macd_aligned = True
        
        # 3. Classify based on TSS
        if tss >= 70: # Strong Trend (e.g. ADX>25 + EMA)
             if trend_direction == 'up':
                 return (MarketRegime.TRENDING_UP, 85.0, f"强上涨趋势 (TSS:{tss} - {','.join(tss_details)})")
             elif trend_direction == 'down':
                 return (MarketRegime.TRENDING_DOWN, 85.0, f"强下跌趋势 (TSS:{tss} - {','.join(tss_details)})")
        
        elif tss >= 30: # Weak Trend
             if trend_direction == 'up':
                 return (MarketRegime.TRENDING_UP, 60.0, f"弱上涨趋势 (TSS:{tss} - {','.join(tss_details)})")
             elif trend_direction == 'down':
                 return (MarketRegime.TRENDING_DOWN, 60.0, f"弱下跌趋势 (TSS:{tss} - {','.join(tss_details)})")
             
        # 4. Fallback to Choppy/Volatile
        if adx < self.adx_choppy_threshold:
            return (
                MarketRegime.CHOPPY,
                70.0,
                f"震荡市（ADX {adx:.1f} < {self.adx_choppy_threshold}）"
            )
            
        # 5. ADX high but no alignment -> Volatile Directionless
        return (
            MarketRegime.VOLATILE_DIRECTIONLESS,
            65.0,
            f"方向不明（ADX {adx:.1f} 但趋势未对齐）"
        )
    
    def _calculate_price_position(self, df: pd.DataFrame, lookback: int = 50) -> Dict:
        """
        计算价格在近期区间中的位置
        
        Returns:
            {
                'position_pct': float,  # 0-100, 0=最低, 100=最高
                'location': str  # 'low', 'middle', 'high'
            }
        """
        try:
            if len(df) < lookback:
                lookback = len(df)
            
            recent_high = df['high'].iloc[-lookback:].max()
            recent_low = df['low'].iloc[-lookback:].min()
            current_price = df['close'].iloc[-1]
            
            if recent_high == recent_low:
                position_pct = 50.0
            else:
                position_pct = ((current_price - recent_low) / (recent_high - recent_low)) * 100
            
            # Clip to 0-100
            position_pct = max(0, min(100, position_pct))
            
            # Determine location
            if position_pct <= 25:
                location = 'low'
            elif position_pct >= 75:
                location = 'high'
            else:
                location = 'middle'
            
            return {
                'position_pct': position_pct,
                'location': location
            }
        except Exception:
            return {'position_pct': 50.0, 'location': 'unknown'}

    def _analyze_choppy_market(self, df: pd.DataFrame, current_bb_width: float, lookback: int = 20) -> Dict:
        """
        CHOPPY 市场专项分析
        
        提供区间交易和突破预警的关键信息：
        1. Squeeze 检测 (布林带收窄)
        2. 支撑阻力识别
        3. 突破概率评估
        4. Mean Reversion 机会
        
        Returns:
            {
                'squeeze_active': bool,          # 是否处于 Squeeze 状态
                'squeeze_intensity': float,      # Squeeze 强度 0-100
                'range': {                       # 区间信息
                    'support': float,
                    'resistance': float,
                    'range_pct': float           # 区间宽度相对于价格的百分比
                },
                'breakout_probability': float,   # 突破概率 0-100
                'breakout_direction': str,       # 可能的突破方向 'up', 'down', 'unknown'
                'mean_reversion_signal': str,    # 'buy_dip', 'sell_rally', 'neutral'
                'consolidation_bars': int,       # 连续震荡K线数量
                'strategy_hint': str             # 策略建议
            }
        """
        try:
            # 1. Squeeze 检测 - 布林带宽度相对于历史值的收窄程度
            squeeze_active = False
            squeeze_intensity = 0.0
            
            if 'bb_upper' in df.columns and 'bb_lower' in df.columns and 'bb_middle' in df.columns:
                # 计算历史布林带宽度
                bb_widths = ((df['bb_upper'] - df['bb_lower']) / df['bb_middle'] * 100).iloc[-lookback:]
                avg_width = bb_widths.mean()
                min_width = bb_widths.min()
                
                # 当前宽度 vs 平均宽度
                if avg_width > 0:
                    width_ratio = current_bb_width / avg_width
                    if width_ratio < 0.7:  # 宽度低于平均70% = Squeeze
                        squeeze_active = True
                        squeeze_intensity = (1 - width_ratio) * 100  # 0-100
            
            # 2. 支撑阻力识别
            recent_high = df['high'].iloc[-lookback:].max()
            recent_low = df['low'].iloc[-lookback:].min()
            current_price = df['close'].iloc[-1]
            
            range_pct = ((recent_high - recent_low) / current_price) * 100 if current_price > 0 else 0
            
            # 3. 价格位置与 Mean Reversion 信号
            position_pct = ((current_price - recent_low) / (recent_high - recent_low) * 100) if (recent_high - recent_low) > 0 else 50
            
            if position_pct <= 20:
                mean_reversion_signal = 'buy_dip'
            elif position_pct >= 80:
                mean_reversion_signal = 'sell_rally'
            else:
                mean_reversion_signal = 'neutral'
            
            # 4. 突破概率评估
            breakout_probability = 0.0
            breakout_direction = 'unknown'
            
            # Squeeze + 价格逼近边界 = 高突破概率
            if squeeze_active:
                breakout_probability += squeeze_intensity * 0.5  # Max 50 from squeeze
                
                # 价格逼近边界增加概率
                if position_pct >= 85:
                    breakout_probability += 30
                    breakout_direction = 'up'
                elif position_pct <= 15:
                    breakout_probability += 30
                    breakout_direction = 'down'
                else:
                    breakout_probability += 10
            
            # 成交量异常检测增加概率
            if 'volume' in df.columns:
                recent_vol = df['volume'].iloc[-5:].mean()
                avg_vol = df['volume'].iloc[-lookback:].mean()
                if recent_vol > avg_vol * 1.5:
                    breakout_probability += 20
            
            breakout_probability = min(100, breakout_probability)
            
            # 5. 连续震荡 K 线计数 (用于判断震荡末期)
            consolidation_bars = 0
            for i in range(1, min(50, len(df))):
                idx = -i
                bar_range = (df['high'].iloc[idx] - df['low'].iloc[idx]) / df['close'].iloc[idx] * 100
                if bar_range < 1.5:  # 波动小于 1.5% 视为震荡
                    consolidation_bars += 1
                else:
                    break
            
            # 6. 策略建议
            if squeeze_active and breakout_probability >= 60:
                if breakout_direction == 'up':
                    strategy_hint = "SQUEEZE_BREAKOUT_LONG: Prepare for upside breakout, set alerts at resistance"
                elif breakout_direction == 'down':
                    strategy_hint = "SQUEEZE_BREAKOUT_SHORT: Prepare for downside breakout, set alerts at support"
                else:
                    strategy_hint = "SQUEEZE_IMMINENT: Volatility expansion expected, wait for direction confirmation"
            elif mean_reversion_signal == 'buy_dip':
                strategy_hint = "MEAN_REVERSION_LONG: Price near support, consider long with tight stop below support"
            elif mean_reversion_signal == 'sell_rally':
                strategy_hint = "MEAN_REVERSION_SHORT: Price near resistance, consider short with tight stop above resistance"
            else:
                strategy_hint = "RANGE_WAIT: No clear edge, wait for price to reach range extremes"
            
            return {
                'squeeze_active': squeeze_active,
                'squeeze_intensity': min(100, max(0, squeeze_intensity)),
                'range': {
                    'support': recent_low,
                    'resistance': recent_high,
                    'range_pct': min(20, max(0, range_pct))
                },
                'breakout_probability': breakout_probability,
                'breakout_direction': breakout_direction,
                'mean_reversion_signal': mean_reversion_signal,
                'consolidation_bars': consolidation_bars,
                'strategy_hint': strategy_hint
            }
            
        except Exception as e:
            return {
                'squeeze_active': False,
                'squeeze_intensity': 0,
                'range': {'support': 0, 'resistance': 0, 'range_pct': 0},
                'breakout_probability': 0,
                'breakout_direction': 'unknown',
                'mean_reversion_signal': 'neutral',
                'consolidation_bars': 0,
                'strategy_hint': 'ANALYSIS_ERROR: Unable to analyze choppy market'
            }


# 测试代码
if __name__ == '__main__':
    # 创建测试数据
    dates = pd.date_range('2025-01-01', periods=100, freq='5min')
    
    # 模拟上涨趋势
    uptrend_prices = 87000 + np.cumsum(np.random.randn(100) * 10 + 5)
    
    df_uptrend = pd.DataFrame({
        'timestamp': dates,
        'close': uptrend_prices,
        'high': uptrend_prices + 50,
        'low': uptrend_prices - 50,
        'sma_20': uptrend_prices - 100,
        'sma_50': uptrend_prices - 200,
        'ema_12': uptrend_prices - 50,
        'ema_26': uptrend_prices - 150,
        'atr': np.full(100, 100),
        'bb_upper': uptrend_prices + 200,
        'bb_middle': uptrend_prices,
        'bb_lower': uptrend_prices - 200
    })
    
    # 模拟震荡市
    choppy_prices = 87000 + np.random.randn(100) * 50
    
    df_choppy = pd.DataFrame({
        'timestamp': dates,
        'close': choppy_prices,
        'high': choppy_prices + 30,
        'low': choppy_prices - 30,
        'sma_20': np.full(100, 87000),
        'sma_50': np.full(100, 87000),
        'ema_12': choppy_prices,
        'ema_26': choppy_prices,
        'atr': np.full(100, 50),
        'bb_upper': choppy_prices + 100,
        'bb_middle': choppy_prices,
        'bb_lower': choppy_prices - 100
    })
    
    detector = RegimeDetector()
    
    print("市场状态检测测试:\n")
    
    print("1. 上涨趋势测试:")
    result = detector.detect_regime(df_uptrend)
    print(f"   状态: {result['regime']}")
    print(f"   信心: {result['confidence']:.1f}%")
    print(f"   ADX: {result['adx']:.1f}")
    print(f"   趋势方向: {result['trend_direction']}")
    print(f"   原因: {result['reason']}")
    print()
    
    print("2. 震荡市测试:")
    result = detector.detect_regime(df_choppy)
    print(f"   状态: {result['regime']}")
    print(f"   信心: {result['confidence']:.1f}%")
    print(f"   ADX: {result['adx']:.1f}")
    print(f"   趋势方向: {result['trend_direction']}")
    print(f"   原因: {result['reason']}")
