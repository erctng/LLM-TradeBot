"""
Historical Data Replay Agent
===================================

Simulate DataSyncAgent, generate MarketSnapshot from historical data
Provide same data interface as live trading for backtest

Author: AI Trader Team
Date: 2025-12-31
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Iterator, Optional, Tuple
from dataclasses import dataclass, field
import pandas as pd
import numpy as np
import os

from src.api.binance_client import BinanceClient
from src.agents.data_sync import MarketSnapshot
from src.utils.logger import log
from src.utils.kline_cache import get_kline_cache


@dataclass
class FundingRateRecord:
    """Funding Rate Record"""
    timestamp: datetime
    funding_rate: float
    mark_price: float


@dataclass
class DataCache:
    """Historical Data Cache"""
    symbol: str
    df_5m: pd.DataFrame
    df_15m: pd.DataFrame
    df_1h: pd.DataFrame
    start_date: datetime
    end_date: datetime
    funding_rates: List['FundingRateRecord'] = field(default_factory=list)  # Funding rate history



class DataReplayAgent:
    """
    Historical Data Replay Agent
    
    Features:
    1. Fetch historical K-line data from Binance
    2. Local cache (Parquet format)
    3. Generate MarketSnapshot at specified timestamp
    4. Simulate real-time data stream for backtest
    """
    
    CACHE_DIR = "data/backtest/cache"  # Backtest-specific cache
    KLINE_DIR = "data/kline"  # Shared K-line cache (prioritized)
    
    def __init__(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        client: BinanceClient = None
    ):
        """
        Initialize data replay agent
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            start_date: Start date "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"
            end_date: End date "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"
            client: Binance client (optional)
        """
        self.symbol = symbol

        start_dt, _ = self._parse_input_date(start_date)
        end_dt, end_has_time = self._parse_input_date(end_date)
        if not end_has_time:
            end_dt = end_dt + timedelta(days=1)

        # Normalize to UTC-naive to match Binance UTC timestamps
        self.start_date = self._to_utc_naive(start_dt)
        self.end_date = self._to_utc_naive(end_dt)
            
        self.client = client or BinanceClient()
        
        # 数据cache
        self.data_cache: Optional[DataCache] = None
        
        # 当前回放位置
        self.current_idx = 0
        self.timestamps: List[datetime] = []
        
        # 最新快照（模拟 DataSyncAgent.latest_snapshot）
        self.latest_snapshot: Optional[MarketSnapshot] = None
        
        # 确保cache目录存在
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        
        # Initialize shared K-line cache for incremental fetching
        self._kline_cache = get_kline_cache()
        
        log.info(f"📼 DataReplayAgent initialized | {symbol} | {self.start_date} to {self.end_date}")
    
    async def load_data(self) -> bool:
        """
        Load historical data (使用统一的 KlineCache)
        
        Prioritize reading from data/kline/{symbol}/*.parquet (shared directory)
        Only fetch incremental data from API
        
        Returns:
            Whether loading was successful
        """
        log.info(f"📥 Fetching historical data from Binance API...")
        
        try:
            await self._fetch_from_api()
            
            if not self._cache_covers_range():
                log.error(
                    "Fetched data does not fully cover requested range "
                    f"(expected 5m {self._expected_start_5m()} -> {self._expected_end_5m()}, "
                    f"cache {self._describe_cache_range()})."
                )
                return False
            
            log.info(f"✅ Data ready: {len(self.timestamps)} timestamps")
            return True
            
        except Exception as e:
            log.error(f"❌ Failed to fetch historical data: {e}")
            return False

    def _parse_input_date(self, value: str) -> Tuple[datetime, bool]:
        """Parse input date, return (datetime, whether includes time)"""
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M"), True
        except ValueError:
            return datetime.strptime(value, "%Y-%m-%d"), False

    def _to_utc_naive(self, dt: datetime) -> datetime:
        """将本地时间转换为UTC-naive，避免与Binance UTC时间轴错位"""
        if dt.tzinfo is None:
            local_tz = datetime.now().astimezone().tzinfo
            dt = dt.replace(tzinfo=local_tz)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    def _utc_timestamp_ms(self, dt: datetime) -> int:
        """UTC-naive -> UTC timestamp (ms)"""
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

    def _describe_cache_range(self) -> str:
        """简要描述cache数据时间范围（用于诊断）"""
        if self.data_cache is None:
            return "cache=None"
        def _range(df: pd.DataFrame) -> str:
            if df is None or df.empty:
                return "empty"
            return f"{df.index.min()} -> {df.index.max()}"
        return f"5m[{_range(self.data_cache.df_5m)}], 15m[{_range(self.data_cache.df_15m)}], 1h[{_range(self.data_cache.df_1h)}]"

    def _expected_start_5m(self) -> datetime:
        return pd.Timestamp(self.start_date).ceil("5min").to_pydatetime()

    def _expected_end_5m(self) -> datetime:
        end_cutoff = self.end_date - timedelta(seconds=1)
        return pd.Timestamp(end_cutoff).floor("5min").to_pydatetime()

    def _cache_covers_range(self) -> bool:
        """检查cache是否覆盖完整回测窗口（含多周期）
        
        Add fault tolerance: allow 1 hour data delay tolerance
        This prevents backtest failure due to Binance data delay
        """
        if self.data_cache is None:
            return False
        df_5m = self.data_cache.df_5m
        df_15m = self.data_cache.df_15m
        df_1h = self.data_cache.df_1h
        if df_5m.empty or df_15m.empty or df_1h.empty:
            return False

        start_5m = pd.Timestamp(self.start_date).ceil("5min")
        start_15m = pd.Timestamp(self.start_date).ceil("15min")
        start_1h = pd.Timestamp(self.start_date).ceil("60min")

        end_cutoff = self.end_date - timedelta(seconds=1)
        end_5m = pd.Timestamp(end_cutoff).floor("5min")
        end_15m = pd.Timestamp(end_cutoff).floor("15min")
        end_1h = pd.Timestamp(end_cutoff).floor("60min")

        # 添加tolerance：允许数据缺失最多1小时（用于处理实时数据延迟）
        tolerance = pd.Timedelta(hours=1)

        # 检查起始时间（严格）
        if df_5m.index.min() > start_5m:
            return False
        if df_15m.index.min() > start_15m:
            return False
        if df_1h.index.min() > start_1h:
            return False

        # 检查结束时间（带tolerance）
        if df_5m.index.max() < (end_5m - tolerance):
            log.warning(f"⚠️ 5m data ends at {df_5m.index.max()}, expected {end_5m} (tolerance: 1h)")
            return False
        if df_15m.index.max() < (end_15m - tolerance):
            log.warning(f"⚠️ 15m data ends at {df_15m.index.max()}, expected {end_15m} (tolerance: 1h)")
            return False
        if df_1h.index.max() < (end_1h - tolerance):
            log.warning(f"⚠️ 1h data ends at {df_1h.index.max()}, expected {end_1h} (tolerance: 1h)")
            return False

        # 如果数据有缺失但在tolerance范围内，Adjusting backtest end time
        actual_end = min(df_5m.index.max(), df_15m.index.max(), df_1h.index.max())
        if actual_end < end_5m:
            log.info(f"📊 Adjusting backtest end time: {self.end_date} → {actual_end} (data availability)")
            self.end_date = actual_end.to_pydatetime()

        return True
    
    def _get_cache_path(self) -> str:
        """生成cache文件路径"""
        # Use simple date string for cache key to maximize hits
        # (Even if precise time is used, we cache the whole day range usually)
        # But here start/end might be mid-day. 
        # Strategy: Cache based on DATE part only to allow reuse for different times on same days.
        start_str = self.start_date.strftime("%Y%m%d")
        
        # Use the DATE part of end_date (minus a microsecond to handle clean midnight?)
        # Actually safest is to use the requested window.
        # But if I request 16:00, and later request 00:00, different cache?
        # Ideally cache should cover the widest range.
        # For simplicity, just use the exact request strings converted to safe chars.
        end_str = self.end_date.strftime("%Y%m%d")
        
        # Include lookback in cache path to ensure invalidation when lookback changes
        lookback_days = 30  # Must match the value in _fetch_from_api
        return os.path.join(
            self.CACHE_DIR,
            f"{self.symbol}_{start_str}_{end_str}_lb{lookback_days}.parquet"
        )
    
    async def _fetch_from_api(self):
        """Fetch historical data from Binance API"""
        # CRITICAL FIX: Need historical data BEFORE backtest period for technical indicators
        # Add lookback period (default 30 days) before start_date
        lookback_days = 30
        extended_start = self.start_date - timedelta(days=lookback_days)
        
        # Calculate total days including lookback
        total_days = (self.end_date - extended_start).days + 1
        
        # Calculate required candles
        # 5m K线：288 per day
        limit_5m = total_days * 288 * 2 # Safety factor
        # 15m K线：96 per day
        limit_15m = total_days * 96 * 2
        # 1h K线：24 per day
        limit_1h = total_days * 24 * 2
        
        log.info(f"📊 Fetching data from {extended_start.date()} to {self.end_date.date()}")
        log.info(f"   Lookback: {lookback_days} days before backtest start")
        
        # Binance API limit: max 1500 per request, need to batch fetch
        # Use KlineCache for local-first fetching
        df_5m = await self._fetch_klines_with_cache("5m", limit_5m)
        df_15m = await self._fetch_klines_with_cache("15m", limit_15m)
        df_1h = await self._fetch_klines_with_cache("1h", limit_1h)
        
        # 获取Funding rate history
        funding_rates = await self._fetch_funding_rates()
        
        # IMPORTANT: Do NOT filter out historical data before start_date here
        # We need it for technical indicator calculation
        # Only filter data AFTER end_date
        df_5m = df_5m[df_5m.index <= self.end_date]
        df_15m = df_15m[df_15m.index <= self.end_date]
        df_1h = df_1h[df_1h.index <= self.end_date]
        
        # 创建cache对象
        self.data_cache = DataCache(
            symbol=self.symbol,
            df_5m=df_5m,
            df_15m=df_15m,
            df_1h=df_1h,
            start_date=self.start_date,
            end_date=self.end_date,
            funding_rates=funding_rates
        )
        
        # Generate timestamp list (based on 5m K-lines)
        all_timestamps = df_5m.index.tolist()
        
        # Filter timestamps to backtest period only
        # Strict inequality for end_date to avoid processing the exact end second if not in data
        self.timestamps = [ts for ts in all_timestamps if self.start_date <= ts < self.end_date]
        
        log.info(f"   Backtest timestamps (5m): {len(self.timestamps)}")
        if self.timestamps:
            log.info(f"   First: {self.timestamps[0]}, Last: {self.timestamps[-1]}")
    
    async def _fetch_funding_rates(self) -> List[FundingRateRecord]:
        """获取Funding rate history数据"""
        funding_records = []
        
        try:
            # Calculate time range
            start_ts = self._utc_timestamp_ms(self.start_date)
            end_ts = self._utc_timestamp_ms(self.end_date)
            
            # Binance API returns max 1000 per call
            current_start = start_ts
            
            # Safety break
            loop_count = 0
            while current_start < end_ts and loop_count < 100:
                loop_count += 1
                funding_data = self.client.client.futures_funding_rate(
                    symbol=self.symbol,
                    startTime=current_start,
                    endTime=end_ts,
                    limit=1000
                )
                
                if not funding_data:
                    break
                
                for record in funding_data:
                    fr = FundingRateRecord(
                        timestamp=datetime.fromtimestamp(record['fundingTime'] / 1000),
                        funding_rate=float(record['fundingRate']),
                        mark_price=float(record.get('markPrice', 0))
                    )
                    funding_records.append(fr)
                
                if len(funding_data) < 1000:
                    break
                
                # 下一批from最后一条时间 +1 开始
                current_start = funding_data[-1]['fundingTime'] + 1
                await asyncio.sleep(0.1)  # Avoid requests too fast
            
            log.info(f"📊 Fetched {len(funding_records)} funding rate records")
            
        except Exception as e:
            log.warning(f"⚠️ Failed to fetch funding rates: {e}")
        
        return funding_records
    
    async def _fetch_klines_with_cache(self, interval: str, total_limit: int) -> pd.DataFrame:
        """
        Fetch K-lines using local cache first, then API for missing data
        
        Optimization: Prioritize local parquet cache, only fetch incremental data from API
        
        Args:
            interval: K-line interval ('5m', '15m', '1h')
            total_limit: Total number of K-lines needed
            
        Returns:
            DataFrame with K-line data
        """
        # Check cache for existing data
        cached_df = self._kline_cache.get_cached_data(self.symbol, interval)
        
        if cached_df is not None and not cached_df.empty:
            # Cache exists - check coverage
            cache_start = cached_df['timestamp'].min() if 'timestamp' in cached_df.columns else None
            cache_end = cached_df['timestamp'].max() if 'timestamp' in cached_df.columns else None
            
            # Calculate lookback period
            lookback_days = 30
            extended_start = self.start_date - timedelta(days=lookback_days)
            extended_start_ms = self._utc_timestamp_ms(extended_start)
            end_ms = self._utc_timestamp_ms(self.end_date)
            
            # Check if cache covers our range
            if cache_start is not None and cache_end is not None:
                cache_start_ms = int(cache_start)
                cache_end_ms = int(cache_end)
                
                if cache_start_ms <= extended_start_ms and cache_end_ms >= end_ms:
                    # Cache fully covers range
                    log.info(f"📦 Cache hit: {self.symbol}/{interval} | Using cached data")
                    
                    # Filter to needed range and convert to proper format
                    df = cached_df.copy()
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)
                    
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    return df
                else:
                    # Cache partial - fetch missing data
                    log.info(f"📦 Cache partial: {self.symbol}/{interval} | Fetching additional data")
                    
                    # Determine what to fetch
                    if cache_end_ms < end_ms:
                        # Need newer data
                        await self._fetch_and_append_to_cache(interval, cache_end_ms + 1, end_ms)
                    
                    if cache_start_ms > extended_start_ms:
                        # Need older data - use batched fetch
                        old_df = await self._fetch_klines_batched(interval, total_limit)
                        if not old_df.empty:
                            # Convert and append
                            old_df_reset = old_df.reset_index()
                            old_df_reset['timestamp'] = ((old_df_reset['timestamp'] - pd.Timestamp("1970-01-01")) // pd.Timedelta('1ms'))
                            self._kline_cache.append_data(self.symbol, interval, old_df_reset.to_dict('records'), retention_days=0)
                    
                    # Get updated cache
                    updated_df = self._kline_cache.get_cached_data(self.symbol, interval)
                    if updated_df is not None:
                        df = updated_df.copy()
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors='coerce')
                        return df
        
        # No cache or insufficient - full fetch via API
        log.info(f"📦 Cache miss: {self.symbol}/{interval} | Fetching from API")
        df = await self._fetch_klines_batched(interval, total_limit)
        
        # Save to cache
        if not df.empty:
            df_for_cache = df.reset_index()
            df_for_cache['timestamp'] = ((df_for_cache['timestamp'] - pd.Timestamp("1970-01-01")) // pd.Timedelta('1ms'))
            self._kline_cache.append_data(self.symbol, interval, df_for_cache.to_dict('records'), retention_days=0)
        
        return df
    
    async def _fetch_and_append_to_cache(self, interval: str, start_ms: int, end_ms: int):
        """Fetch K-lines from API and append to cache"""
        try:
            current_start = start_ms
            total_appended = 0
            
            while current_start < end_ms:
                klines = self.client.client.futures_klines(
                    symbol=self.symbol,
                    interval=interval,
                    startTime=current_start,
                    endTime=end_ms,
                    limit=1000
                )
                
                if not klines:
                    break
                    
                # Convert to cache format
                klines_dict = []
                for k in klines:
                    klines_dict.append({
                        'timestamp': k[0],
                        'open': float(k[1]),
                        'high': float(k[2]),
                        'low': float(k[3]),
                        'close': float(k[4]),
                        'volume': float(k[5]),
                    })
                
                self._kline_cache.append_data(self.symbol, interval, klines_dict, retention_days=0)
                total_appended += len(klines_dict)
                
                if len(klines) < 1000:
                    break
                    
                current_start = klines[-1][0] + 1
                import asyncio
                await asyncio.sleep(0.1)
                
            log.debug(f"📦 Appended {total_appended} rows to {self.symbol}/{interval} cache")
                
        except Exception as e:
            log.warning(f"Failed to fetch incremental data: {e}")
    
    async def _fetch_klines_batched(self, interval: str, total_limit: int) -> pd.DataFrame:
        """分批获取 K 线数据"""
        all_klines = []
        batch_size = 1000  # Binance 推荐的批次大小
        
        # 计算结束Timestamp
        end_ts = self._utc_timestamp_ms(self.end_date)
        
        remaining = total_limit
        current_end = end_ts
        loop_count = 0
        
        while remaining > 0 and loop_count < 200: # Safety break
            loop_count += 1
            limit = min(batch_size, remaining)
            
            try:
                klines = self.client.client.futures_klines(
                    symbol=self.symbol,
                    interval=interval,
                    endTime=current_end,
                    limit=limit
                )
                
                if not klines:
                    break
                
                all_klines = klines + all_klines  # 倒序插入
                
                # 更新下一批的结束时间（取最早一根的开始时间 - 1）
                current_end = klines[0][0] - 1
                remaining -= len(klines)
                
                # Avoid requests too fast
                await asyncio.sleep(0.1)
                
            except Exception as e:
                log.warning(f"Batch fetch error: {e}")
                break
        
        # 转换为 DataFrame
        return self._klines_to_dataframe(all_klines)
    
    def _klines_to_dataframe(self, klines: List) -> pd.DataFrame:
        """将 K 线列表转换为 DataFrame"""
        if not klines:
            return pd.DataFrame()
        
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        
        # 转换数据类型
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
            df[col] = df[col].astype(float)
        
        df['trades'] = df['trades'].astype(int)
        
        return df[['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades']]
    
    def _filter_date_range(self, df: pd.DataFrame) -> pd.DataFrame:
        """过滤日期范围"""
        if df.empty:
            return df
        # Use < instead of <= since end_date is now strictly parsed
        return df[(df.index >= self.start_date) & (df.index < self.end_date)]
    
    def _save_to_cache(self, cache_path: str):
        """保存数据tocache"""
        if self.data_cache is None:
            return
        
        # 合并所有数据
        cache_data = {
            'df_5m': self.data_cache.df_5m,
            'df_15m': self.data_cache.df_15m,
            'df_1h': self.data_cache.df_1h,
            'funding_rates': [
                {'timestamp': fr.timestamp, 'funding_rate': fr.funding_rate, 'mark_price': fr.mark_price}
                for fr in self.data_cache.funding_rates
            ],
            # Save date boundaries to verify cache validity
            'start_date': self.start_date,
            'end_date': self.end_date
        }
        
        # 使用 pickle 保存（支持多个 DataFrame）
        import pickle
        with open(cache_path, 'wb') as f:
            pickle.dump(cache_data, f)
    
    def _load_from_cache(self, cache_path: str):
        """fromcache加载数据"""
        import pickle
        with open(cache_path, 'rb') as f:
            cache_data = pickle.load(f)
        
        # 加载CapitalRate（兼容旧cache）
        funding_rates = []
        if 'funding_rates' in cache_data:
            for fr_dict in cache_data['funding_rates']:
                funding_rates.append(FundingRateRecord(
                    timestamp=fr_dict['timestamp'],
                    funding_rate=fr_dict['funding_rate'],
                    mark_price=fr_dict.get('mark_price', 0)
                ))
        
        # Reconstruct DataCache
        self.data_cache = DataCache(
            symbol=self.symbol,
            df_5m=cache_data['df_5m'],
            df_15m=cache_data['df_15m'],
            df_1h=cache_data['df_1h'],
            start_date=self.start_date,
            end_date=self.end_date,
            funding_rates=funding_rates
        )
        
        self.timestamps = [ts for ts in self.data_cache.df_5m.index.tolist() if self.start_date <= ts < self.end_date]
        
        log.info(f"   Date comparison: start_date={self.start_date}, end_date={self.end_date}")
        if not self.timestamps:
            log.warning(f"   ⚠️ Cache loaded but zero timestamps in requested range!")
        else:
            log.info(f"   Cached range: {self.timestamps[0]} to {self.timestamps[-1]}")
            log.info(f"   Backtest timestamps: {len(self.timestamps)}")
    
    def get_snapshot_at(self, timestamp: datetime, lookback: int = 1000) -> MarketSnapshot:
        """
        获取指定时间点的市场快照
        
        Args:
            timestamp: 目标时间点
            lookback: 回看的 K 线Quantity (5m candles). Defaults to 1000 (~3.5 days) to ensure enough 1h data.
            
        Returns:
            MarketSnapshot 对象（与 DataSyncAgent 兼容）
        """
        if self.data_cache is None:
            raise ValueError("Data not loaded. Call load_data() first.")
        
        # 获取截止to timestamp 的数据
        # Ensure we have enough data for 1h analysis (need > 60 candles)
        # 1000 5m candles = 83 1h candles.
        
        df_5m = self.data_cache.df_5m[self.data_cache.df_5m.index <= timestamp].tail(lookback)
        
        # For 15m and 1h, we need at least 100 candles to be safe for indicators
        lb_15m = max(lookback // 3, 100)
        lb_1h = max(lookback // 12, 100)
        
        df_15m = self.data_cache.df_15m[self.data_cache.df_15m.index <= timestamp].tail(lb_15m)
        df_1h = self.data_cache.df_1h[self.data_cache.df_1h.index <= timestamp].tail(lb_1h)
        
        # Stable view: 排除最后一根（未完成）
        # Live view: 最后一根（作为 Dict）
        live_5m_dict = df_5m.iloc[-1].to_dict() if len(df_5m) > 0 else {}
        live_15m_dict = df_15m.iloc[-1].to_dict() if len(df_15m) > 0 else {}
        live_1h_dict = df_1h.iloc[-1].to_dict() if len(df_1h) > 0 else {}
        
        funding_snapshot = {}
        fr_record = self.get_funding_rate_at(timestamp)
        if fr_record:
            funding_snapshot = {
                'funding_rate': fr_record.funding_rate,
                'timestamp': fr_record.timestamp,
                'mark_price': fr_record.mark_price
            }

        snapshot = MarketSnapshot(
            stable_5m=df_5m.iloc[:-1] if len(df_5m) > 1 else df_5m,
            stable_15m=df_15m.iloc[:-1] if len(df_15m) > 1 else df_15m,
            stable_1h=df_1h.iloc[:-1] if len(df_1h) > 1 else df_1h,
            live_5m=live_5m_dict,
            live_15m=live_15m_dict,
            live_1h=live_1h_dict,
            timestamp=timestamp,
            alignment_ok=self._check_alignment(df_5m, df_15m, df_1h),
            fetch_duration=0.0,
            binance_funding=funding_snapshot,
            binance_oi={},
            symbol=self.symbol
        )
        
        self.latest_snapshot = snapshot
        return snapshot
    
    def iterate_timestamps(self, step: int = 1) -> Iterator[datetime]:
        """
        迭代所有回测时间点
        
        Args:
            step: 步长（1 = 每 5 分钟，3 = 每 15 分钟，12 = 每小时）
            
        Yields:
            datetime 时间点
        """
        for i in range(0, len(self.timestamps), step):
            self.current_idx = i
            yield self.timestamps[i]
    
    def get_current_price(self) -> float:
        """
        Get current price
        
        CRITICAL FIX (Cycle 2):
        防止 Look-ahead Bias：
        返回当前 K 线的 Open 价格，而不是 Close 价格。
        在回测时刻 T，我们只能看to T 时刻的开盘价，看不to T+5m 的收盘价。
        """
        if self.latest_snapshot is None:
            return 0.0
        
        live = self.latest_snapshot.live_5m
        if isinstance(live, dict):
            # 使用 OPEN 价格
            return float(live.get('open', 0.0))
        elif hasattr(live, 'empty') and not live.empty:
            # 使用 OPEN 价格
            return float(live['open'].iloc[-1])
        return 0.0
    
    def get_open_price(self) -> float:
        """
        获取当前 K 线的开盘价
        
        用于防止 Look-ahead Bias：
        - 信号计算使用 bar[i-1] 的数据
        - 交易执行使用 bar[i] 的开盘价
        """
        if self.latest_snapshot is None:
            return 0.0
        
        live = self.latest_snapshot.live_5m
        if isinstance(live, dict):
            return float(live.get('open', 0.0))
        elif hasattr(live, 'empty') and not live.empty:
            return float(live['open'].iloc[-1])
        return 0.0
    
    def get_previous_close_price(self) -> float:
        """
        获取上一根 K 线的收盘价
        
        用于 Look-ahead Bias 防护的信号计算
        """
        if self.latest_snapshot is None:
            return 0.0
        
        stable = self.latest_snapshot.stable_5m
        if hasattr(stable, 'empty') and not stable.empty:
            return float(stable['close'].iloc[-1])
        return self.get_open_price()
    
    def get_progress(self) -> Tuple[int, int, float]:
        """获取回放进度"""
        total = len(self.timestamps)
        current = self.current_idx
        pct = (current / total * 100) if total > 0 else 0
        return current, total, pct
    
    def get_funding_rate_at(self, timestamp: datetime) -> Optional[FundingRateRecord]:
        """
        获取指定时间点或之前最近的CapitalRate
        
        Binance CapitalRate每 8 小时结算（UTC 00:00, 08:00, 16:00）
        """
        if self.data_cache is None or not self.data_cache.funding_rates:
            return None
        
        # 找toTimestamp之前最近的CapitalRate
        latest_fr = None
        for fr in self.data_cache.funding_rates:
            if fr.timestamp <= timestamp:
                latest_fr = fr
            else:
                break
        
        return latest_fr
    
    def is_funding_settlement_time(self, timestamp: datetime) -> bool:
        """
        检查是否是CapitalRateSettlement timestamp
        
        Binance 合约CapitalRateSettlement timestamp：UTC 00:00, 08:00, 16:00
        """
        if timestamp.tzinfo is not None:
            ts_utc = timestamp.astimezone(timezone.utc)
        else:
            ts_utc = timestamp
        utc_hour = ts_utc.hour
        utc_minute = ts_utc.minute
        
        # 检查是否为结算时刻（允许几分钟误差）
        if utc_hour in [0, 8, 16] and utc_minute < 10:
            return True
        return False

    def _check_alignment(
        self,
        df_5m: pd.DataFrame,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame
    ) -> bool:
        """检查多周期数据对齐性（基于索引Timestamp）"""
        if df_5m.empty or df_15m.empty or df_1h.empty:
            return False

        try:
            t5m = df_5m.index[-1]
            t15m = df_15m.index[-1]
            t1h = df_1h.index[-1]

            diff_5m_15m = abs((t5m - t15m).total_seconds())
            diff_5m_1h = abs((t5m - t1h).total_seconds())

            max_diff_15m = 15 * 60
            max_diff_1h = 60 * 60

            return diff_5m_15m <= max_diff_15m and diff_5m_1h <= max_diff_1h
        except Exception:
            return False
    
    def get_funding_rate_for_settlement(self, timestamp: datetime) -> Optional[float]:
        """
        获取结算时刻适用的CapitalRate（仅在结算时刻返回，否则返回None）
        """
        if not self.is_funding_settlement_time(timestamp):
            return None
        
        fr = self.get_funding_rate_at(timestamp)
        if fr and abs((fr.timestamp - timestamp).total_seconds()) < 600:  # 10分钟内
            return fr.funding_rate
        return None
    
    # ========== DataSyncAgent 兼容接口 ==========
    
    async def fetch_all_timeframes(self, symbol: str = None, limit: int = 300) -> MarketSnapshot:
        """
        兼容 DataSyncAgent.fetch_all_timeframes 接口
        
        在回测Mode下，返回当前时间点的快照
        """
        if self.current_idx < len(self.timestamps):
            timestamp = self.timestamps[self.current_idx]
            return self.get_snapshot_at(timestamp, lookback=limit)
        else:
            raise IndexError("Replay finished, no more data")
    
    def get_live_price(self, timeframe: str = '5m') -> float:
        """兼容 DataSyncAgent.get_live_price 接口"""
        return self.get_current_price()
    
    def get_stable_dataframe(self, timeframe: str = '5m') -> pd.DataFrame:
        """兼容 DataSyncAgent.get_stable_dataframe 接口"""
        if self.latest_snapshot is None:
            return pd.DataFrame()
        
        if timeframe == '5m':
            return self.latest_snapshot.stable_5m
        elif timeframe == '15m':
            return self.latest_snapshot.stable_15m
        elif timeframe == '1h':
            return self.latest_snapshot.stable_1h
        else:
            return self.latest_snapshot.stable_5m


# 测试函数
async def test_data_replay():
    """测试数据回放器"""
    print("\n" + "=" * 60)
    print("🧪 Testing DataReplayAgent")
    print("=" * 60)
    
    # 创建回放器（测试 7 天数据）
    replay = DataReplayAgent(
        symbol="BTCUSDT",
        start_date="2024-12-01",
        end_date="2024-12-07"
    )
    
    # 加载数据
    success = await replay.load_data()
    print(f"\n✅ Data loaded: {success}")
    
    if success:
        # 迭代前 5 个时间点
        print("\n📊 First 5 timestamps:")
        for i, ts in enumerate(replay.iterate_timestamps()):
            if i >= 5:
                break
            snapshot = replay.get_snapshot_at(ts)
            price = replay.get_current_price()
            print(f"   {i+1}. {ts} | Price: ${price:.2f}")
        
        # 显示进度
        current, total, pct = replay.get_progress()
        print(f"\n📈 Progress: {current}/{total} ({pct:.1f}%)")
    
    print("\n✅ DataReplayAgent test complete!")


if __name__ == "__main__":
    asyncio.run(test_data_replay())
