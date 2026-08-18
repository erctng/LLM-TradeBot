"""
量化策略师 (The Strategist) Agent - 重构版

职责：
按时间周期组织技术分析，而非按指标类型
- 6小时分析：完整技术指标集
- 2小时分析：完整技术指标集
- 半小时分析：完整技术指标集

优化点：
- 时间周期为中心的组织方式
- 便于LLM理解每个时间周期的完整技术面
- 扩展指标集：EMA, MA, BOLL, RSI, MACD, KDJ, ATR, OBV
"""

import pandas as pd
from typing import Dict
from dataclasses import asdict

from src.agents.data_sync import MarketSnapshot
from src.utils.logger import log
from src.utils.oi_tracker import oi_tracker
from src.agents.regime_detector_agent import RegimeDetector
import numpy as np


class QuantAnalystAgent:
    """
    量化策略师 (The Strategist)
    
    提供情绪分析和OI燃料验证
    技术指标分析现在直接在main.py中使用真实1h/15m/5m数据
    
    New Capabilities (2026-01-11):
    - Trap Detection (Rapid Rise Slow Fall)
    - Dead Cat Bounce Detection (Weak Rebound)
    - Divergence Detection (High Price Low Volume)
    - Accumulation Detection (Bottom Stability)
    """
    
    def __init__(self):
        """初始化量化策略师"""
        self.regime_detector = RegimeDetector()
        log.info("👨‍🔬 The Strategist (QuantAnalyst Agent) initialized - Full Analysis Mode + Pattern Recognition")
        
    @staticmethod
    def calculate_ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()
    
    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        loss = loss.replace(0, 1e-10)
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def calculate_kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9, m1: int = 3, m2: int = 3):
        low_min = low.rolling(window=n).min()
        high_max = high.rolling(window=n).max()
        rsv = 100 * (close - low_min) / (high_max - low_min)
        k = rsv.ewm(alpha=1/m1, adjust=False).mean()
        d = k.ewm(alpha=1/m2, adjust=False).mean()
        j = 3 * k - 2 * d
        return k, d, j

    @staticmethod
    def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, adjust=False).mean()
        
    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, window: int = 20, num_std: float = 2.0):
        rolling_mean = series.rolling(window=window).mean()
        rolling_std = series.rolling(window=window).std()
        upper_band = rolling_mean + (rolling_std * num_std)
        lower_band = rolling_mean - (rolling_std * num_std)
        return upper_band, lower_band
        
    def analyze_trend(self, df: pd.DataFrame) -> Dict:
        """Calculate trend score (-100 to +100)"""
        if df is None or len(df) < 60:
            return {'score': 0, 'signal': 'neutral', 'details': {}}
            
        close = df['close']
        ema20 = self.calculate_ema(close, 20)
        ema60 = self.calculate_ema(close, 60)
        
        curr_close = close.iloc[-1]
        curr_ema20 = ema20.iloc[-1]
        curr_ema60 = ema60.iloc[-1]
        
        score = 0
        details = {'ema_status': 'neutral'}
        
        if curr_close > curr_ema20 > curr_ema60:
            score = 60
            details['ema_status'] = 'bullish_alignment'
        elif curr_close < curr_ema20 < curr_ema60:
            score = -60
            details['ema_status'] = 'bearish_alignment'
        elif curr_close > curr_ema20 and curr_ema20 < curr_ema60:
             score = 20 # Potential reversal up
             details['ema_status'] = 'potential_reversal_up'
        elif curr_close < curr_ema20 and curr_ema20 > curr_ema60:
             score = -20 # Potential reversal down
             details['ema_status'] = 'potential_reversal_down'
             
        return {'score': score, 'signal': 'long' if score > 0 else 'short', 'details': details}

    def analyze_oscillator(self, df: pd.DataFrame) -> Dict:
        """Calculate oscillator score (-100 to +100)"""
        if df is None or len(df) < 30:
            return {'score': 0, 'signal': 'neutral', 'details': {}}
            
        close = df['close']
        high = df['high']
        low = df['low']
        
        rsi = self.calculate_rsi(close, 14)
        curr_rsi = rsi.iloc[-1]
        
        k, d, j = self.calculate_kdj(high, low, close)
        curr_j = j.iloc[-1]
        
        score = 0
        details = {'rsi_value': round(curr_rsi, 1), 'kdj_j': round(curr_j, 1)}
        
        if curr_rsi < 30:
            score += 40 # Oversold -> Bullish
        elif curr_rsi > 70:
            score -= 40 # Overbought -> Bearish
            
        if curr_j < 20:
             score += 30
        elif curr_j > 80:
             score -= 30
             
        return {'score': score, 'signal': 'long' if score > 0 else 'short', 'details': details}

    def analyze_market_traps(self, df: pd.DataFrame) -> Dict:
        """
        识别市场陷阱和特殊形态 (User Experience Logic)
        
        Args:
            df: 1h timeframe DataFrame
            
        Returns:
            Dict containing trap flags and details
        """
        if df is None or len(df) < 50:
            return {'details': {}}
            
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']
        
        # 结果字典
        traps = {
            'bull_trap_risk': False,      # 诱多风险 (急速上涨后缓跌)
            'bear_trap_risk': False,      # 诱空风险 (急速下跌后缓涨 - 虽然少见但对称)
            'weak_rebound': False,        # 弱反弹 (暴跌后无量反弹 - 别幻想抄底)
            'volume_divergence': False,   # 量价背离 (高位缩量 - 庄家出货)
            'accumulation': False,        # 底部吸筹 (底部放量不跌)
            'panic_bottom': False,        # 恐慌抛售 (逆向看多)
            'fomo_top': False,            # FOMO 顶部 (逆向看空)
            'details': {}
        }
        
        # 1. 检测 "涨得快跌得慢" (Rapid Rise, Slow Fall) -> 诱多/出货
        # 逻辑：过去N根K线的上涨速度显著大于最近M根K线的下跌速度，且最近表现为阴跌
        recent_window = 10
        if len(close) > recent_window + 5:
            # 计算近期斜率
            # 简单起见，用 (Price_end - Price_start) / Bars
            
            # 寻找最近的一个显著高点
            max_idx = close.iloc[-20:].idxmax()
            
            # 如果高点在比较近的位置（比如5-10根K线前），且之后是缓慢下跌
            # 简单的形态学识别比较难，这里用波动率和涨跌幅特征
            
            # 特征：最近5根K线主要是阴线，但跌幅很小，而之前的5根K线有大阳线
            recent_5_returns = close.pct_change().iloc[-5:]
            prev_5_returns = close.pct_change().iloc[-10:-5]
            
            down_days = (recent_5_returns < 0).sum()
            avg_drop = recent_5_returns[recent_5_returns < 0].mean() if down_days > 0 else 0
            
            max_rise = prev_5_returns.max()
            
            if down_days >= 3 and abs(avg_drop) < 0.005 and max_rise > 0.02:
                # 最近常跌但跌幅小，之前有大涨
                traps['bull_trap_risk'] = True
                traps['details']['pattern'] = "rapid_rise_slow_fall"
        
        # 2. 检测 "弱反弹" (Weak Rebound after Crash)
        # 逻辑：前期有暴跌，随后反弹幅度小且成交量低
        crash_threshold = -0.05 # 5% drop
        if len(close) > 20:
            # 检查主要下跌段
            rolling_min = close.rolling(12).min()
            rolling_max = close.rolling(12).max()
            drop_pct = (rolling_min.iloc[-5] - rolling_max.iloc[-15]) / rolling_max.iloc[-15]
            
            if drop_pct < crash_threshold:
                # 刚刚经历过暴跌
                # 检查反弹力度
                curr_price = close.iloc[-1]
                bounce_pct = (curr_price - rolling_min.iloc[-5]) / rolling_min.iloc[-5]
                
                # 检查成交量
                avg_vol = volume.iloc[-20:].mean()
                curr_vol_avg = volume.iloc[-3:].mean()
                
                if bounce_pct < 0.02 and curr_vol_avg < avg_vol * 0.8:
                    traps['weak_rebound'] = True
                    traps['details']['pattern'] = "weak_rebound_low_vol"

        # 3. 检测 "高位无量" (High Price, Low Volume - Divergence)
        # 逻辑：价格创新高，但成交量未能确认
        if len(close) > 20:
            current_price = close.iloc[-1]
            recent_high = high.iloc[-20:].max()
            
            if current_price >= recent_high * 0.98: # 接近高位
                avg_vol_long = volume.iloc[-50:-10].mean()
                avg_vol_short = volume.iloc[-5:].mean()
                
                if avg_vol_short < avg_vol_long * 0.7:
                     traps['volume_divergence'] = True
                     traps['details']['div'] = "high_price_low_vol"
        
        # 4. 检测 "底部放量不跌" (Accumulation)
        # 逻辑：价格在低位横盘，但成交量持续放大 (Indicates smart money buying)
        if len(close) > 20:
            current_price = close.iloc[-1]
            recent_low = low.iloc[-30:].min()
            
            if current_price <= recent_low * 1.05: # 接近低位
                # 价格波动率低
                volatility = close.iloc[-10:].std() / close.iloc[-10:].mean()
                
                # 成交量放大
                avg_vol_long = volume.iloc[-50:-10].mean()
                avg_vol_short = volume.iloc[-5:].mean()
                
                if volatility < 0.005 and avg_vol_short > avg_vol_long * 1.2:
                    traps['accumulation'] = True
                    traps['details']['pattern'] = "bottom_accumulation"

        # 5. 检测 "逆向情绪" (Contrarian Emotion)
        # 逻辑：利用布林带和RSI极端值识别市场情绪极点
        if len(close) > 20:
            current_price = close.iloc[-1]
            current_rsi = self.calculate_rsi(close).iloc[-1]
            upper, lower = self.calculate_bollinger_bands(close)
            curr_upper = upper.iloc[-1]
            curr_lower = lower.iloc[-1]
            
            avg_vol = volume.iloc[-20:].mean()
            curr_vol = volume.iloc[-1]
            
            # 恐慌抛售 (Panic Selling) -> 看多机会
            # 跌破下轨 + RSI超卖 + 放量 (恐慌盘涌出)
            if current_price < curr_lower and current_rsi < 25 and curr_vol > avg_vol * 2.0:
                traps['panic_bottom'] = True
                traps['details']['emotion'] = "panic_selling_oversold"
                
            # FOMO 顶部 (FOMO Exhaustion) -> 看空机会
            # 突破上轨 + RSI超买 + 放量 (最后接盘侠)
            if current_price > curr_upper and current_rsi > 75 and curr_vol > avg_vol * 2.5:
                traps['fomo_top'] = True
                traps['details']['emotion'] = "fomo_top_overbought"

        return traps

    async def analyze_all_timeframes(self, snapshot: MarketSnapshot) -> Dict:
        """
        执行完整技术分析
        """
        # 1. 情绪分析
        sentiment = self._analyze_sentiment(snapshot)
        
        # 2. 趋势分析
        t_5m = self.analyze_trend(snapshot.stable_5m)
        t_15m = self.analyze_trend(snapshot.stable_15m)
        t_1h = self.analyze_trend(snapshot.stable_1h)
        
        # 3. 震荡分析
        o_5m = self.analyze_oscillator(snapshot.stable_5m)
        o_15m = self.analyze_oscillator(snapshot.stable_15m)
        o_1h = self.analyze_oscillator(snapshot.stable_1h)
        
        # 4. Volatility Analysis (ATR)
        volatility = {'atr_1h': 0.0, 'atr_15m': 0.0, 'atr_5m': 0.0}
        for p, df in [('1h', snapshot.stable_1h), ('15m', snapshot.stable_15m), ('5m', snapshot.stable_5m)]:
             if df is not None and len(df) > 20:
                 atr = self.calculate_atr(df['high'], df['low'], df['close']).iloc[-1]
                 volatility[f'atr_{p}'] = round(atr, 4)
        
        # 5. 计算综合得分
        total_trend_score = (t_5m['score'] + t_15m['score'] + t_1h['score']) / 3
        total_osc_score = (o_5m['score'] + o_15m['score'] + o_1h['score']) / 3
        
        # 6. 市场体制检测 (Using 1h for backbone regime)
        regime = self.regime_detector.detect_regime(snapshot.stable_1h) if snapshot.stable_1h is not None else {}
        
        # 7. 陷阱与形态检测 (User Logic Integration)
        traps = self.analyze_market_traps(snapshot.stable_1h)
        
        result = {
            'symbol': snapshot.symbol,  # 🔧 FIX: Include symbol for DecisionCoreAgent's OvertradingGuard
            'sentiment': sentiment,
            'volatility': volatility,
            'regime': regime,
            'traps': traps,  # New Field
            # 保留空的占位符以兼容
            'timeframe_6h': {}, 
            'timeframe_2h': {},
            'timeframe_30m': {},
            
            'trend': {
                'trend_5m_score': t_5m['score'],
                'trend_15m_score': t_15m['score'],
                'trend_1h_score': t_1h['score'],
                'total_trend_score': total_trend_score,
                'trend_5m': t_5m,
                'trend_15m': t_15m,
                'trend_1h': t_1h
            },
            'oscillator': {
                'osc_5m_score': o_5m['score'],
                'osc_15m_score': o_15m['score'],
                'osc_1h_score': o_1h['score'],
                'total_osc_score': total_osc_score,
                'oscillator_5m': o_5m,
                'oscillator_15m': o_15m,
                'oscillator_1h': o_1h
            },
            'overall_score': (total_trend_score + total_osc_score) / 2
        }
        
        return result
    
    def analyze(self, snapshot: MarketSnapshot) -> Dict:
        return {}

    def _analyze_sentiment(self, snapshot: MarketSnapshot) -> Dict:
        details = {}
        b_funding = getattr(snapshot, 'binance_funding', {})
        has_data = False
        score = 0
        
        if b_funding and 'funding_rate' in b_funding:
            has_data = True
            funding_rate = float(b_funding['funding_rate']) * 100
            details['funding_rate'] = funding_rate
            if funding_rate > 0.05: score -= 30
            elif funding_rate > 0.01: score -= 15
            elif funding_rate < -0.05: score += 30
            elif funding_rate < -0.01: score += 15
        
        # Open interest réel si l'historique couvre la fenêtre, sinon proxy
        # volume. Les deux ne mesurent pas la même chose — l'OI décrit le
        # positionnement net, le volume la rotation — donc la provenance est
        # remontée jusqu'au prompt : un agent à qui l'on présente un volume
        # comme de l'open interest raisonne sur une donnée qui n'existe pas.
        change_pct = 0.0
        fuel_signal = "neutral"
        oi_source = "unavailable"

        symbol = getattr(snapshot, 'symbol', None)
        if symbol and oi_tracker.has_coverage(symbol, hours=24):
            change_pct = oi_tracker.get_change_pct(symbol, hours=24)
            oi_source = "open_interest"
            has_data = True

        df_1h = snapshot.stable_1h
        if oi_source == "unavailable" and df_1h is not None and len(df_1h) >= 24:
            has_data = True
            current_vol = df_1h['volume'].iloc[-1]
            avg_vol = df_1h['volume'].iloc[-25:-1].mean()
            if avg_vol > 0:
                vol_ratio = current_vol / avg_vol
                # Écrêtage à ±200 % : au-delà, la valeur sature et cesse de
                # discriminer. Elle ne doit donc pas être lue comme une mesure.
                change_pct = max(min((vol_ratio - 1) * 100, 200), -100)
                oi_source = "volume_proxy"

        if oi_source != "unavailable":
            details['oi_change_24h_pct'] = change_pct
            details['oi_source'] = oi_source
            if change_pct > 50:
                score += 20
                fuel_signal = "strong"
            elif change_pct > 20:
                score += 10
                fuel_signal = "moderate"
            elif change_pct < -50:
                score -= 10
                fuel_signal = "weak"

        oi_fuel = {
            'oi_change_24h': change_pct,
            'fuel_signal': fuel_signal,
            'fuel_score': min(100, max(-100, int(change_pct))),
            'whale_trap_risk': False,
            'fuel_strength': fuel_signal,
            'oi_source': oi_source,
            'is_proxy': oi_source != "open_interest",
        }

        return {
            'score': score if has_data else 0,
            'details': details,
            'has_data': has_data,
            'total_sentiment_score': score if has_data else 0,
            'oi_change_24h_pct': change_pct,
            'oi_source': oi_source,
            'oi_fuel': oi_fuel,
        }
