"""
Sentiment Agent - Market Intelligence & Sentiment Analysis
"""

import requests
import json
import re
from typing import Dict, Optional
from src.config import Config
from src.utils.logger import log
from src.llm import create_client, LLMConfig

class SentimentAgent:
    """
    Market Intelligence Agent
    
    Input: Global market news / AI-Trader signals feed
    Output: Market sentiment (BULLISH/BEARISH/NEUTRAL)
    """
    
    def __init__(self, config: Config):
        llm_config = config.llm
        provider = llm_config.get('provider', 'deepseek')
        api_keys = llm_config.get('api_keys', {})
        api_key = api_keys.get(provider)
        
        if not api_key and provider == 'deepseek':
            api_key = config.deepseek.get('api_key')
        
        if not api_key:
            api_key = "dummy-key-will-fail"
            
        self.client = create_client(provider, LLMConfig(
            api_key=api_key,
            base_url=llm_config.get('base_url'),
            model=llm_config.get('model') or (config.deepseek.get('model', 'deepseek-chat') if provider == 'deepseek' else None),
            temperature=0.3,
            max_tokens=300
        ))
        
        log.info("📰 Sentiment Agent initialized")
        
    def get_name(self) -> str:
        return "SentimentAgent"
        
    def analyze(self, data: Dict) -> Dict:
        """
        Analyze market sentiment based on recent AI-Trader strategies
        """
        try:
            symbol = data.get('symbol', 'BTCUSDT')
            feed_data = ""
            
            ls_ratio = data.get('ls_ratio')
            if ls_ratio:
                long_pct = ls_ratio.get('long_account', 0) * 100
                short_pct = ls_ratio.get('short_account', 0) * 100
                ratio = ls_ratio.get('long_short_ratio', 1.0)
                feed_data += f"[Order Flow] Retail Sentiment - Longs: {long_pct:.1f}%, Shorts: {short_pct:.1f}%, Ratio: {ratio}\n"
                feed_data += f"If Longs > 65%, the market is over-leveraged long (bearish signal, risk of long squeeze).\n"
                feed_data += f"If Shorts > 65%, the market is over-leveraged short (bullish signal, risk of short squeeze).\n\n"
            
            try:
                resp = requests.get("https://ai4trade.ai/api/signals/feed?limit=3", timeout=3)
                if resp.status_code == 200:
                    signals = resp.json().get('signals', [])
                    for s in signals:
                        feed_data += f"- {s.get('agent_name')}: {s.get('content')}\n"
            except Exception as e:
                log.debug(f"AI feed unavailable: {e}")
                
            if not feed_data:
                feed_data = "No recent market intelligence or order flow data available."
                
            prompt = f"Analyze the following market intelligence and order flow for {symbol}:\n\n{feed_data}\n\nWhat is the overall sentiment? Output ONLY a JSON with 'stance' (BULLISH/BEARISH/NEUTRAL) and 'reasoning' (1 sentence)."
            
            response = self.client.chat(
                system_prompt="You are a market sentiment analyst.",
                user_prompt=prompt
            )
            
            content = response.content.strip()
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                content = match.group(0)
            
            parsed = json.loads(content)
            stance = parsed.get('stance', 'NEUTRAL').upper()
            if stance not in ['BULLISH', 'BEARISH', 'NEUTRAL']:
                stance = 'NEUTRAL'
            
            log.info(f"📰 Sentiment Agent: {stance} for {symbol}")
            return {
                'stance': stance,
                'analysis': parsed.get('reasoning', ''),
                'metadata': {'source': 'ai-trader-feed'}
            }
        except Exception as e:
            log.error(f"❌ Sentiment Agent Error: {e}")
            return {'stance': 'NEUTRAL', 'analysis': 'Error analyzing sentiment', 'metadata': {}}
