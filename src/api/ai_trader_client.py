import threading
import requests
from typing import Optional
from datetime import datetime

from src.utils.logger import log

class AITraderClient:
    """
    Client for interacting with the AI-Trader platform (ai4trade.ai).
    Handles publishing real-time trading signals (TradeSync).
    """
    
    BASE_URL = "https://ai4trade.ai/api"
    
    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.enabled = bool(token and token.strip())
        if self.enabled:
            log.info("🔌 AI-Trader Client initialized (TradeSync Enabled)")
        else:
            log.info("🔌 AI-Trader Client initialized (TradeSync Disabled - No token provided)")
    
    def publish_trade(self, internal_action: str, symbol: str, price: float, quantity: float, content: str = "") -> None:
        """
        Publish a real-time trade signal to AI-Trader followers.
        Executed in a background thread to prevent blocking the main trading engine.
        
        Args:
            internal_action: 'open_long', 'open_short', 'close_long', 'close_short'
            symbol: Trading pair (e.g., 'BTCUSDT')
            price: Execution price
            quantity: Trade volume
            content: Optional context or reasoning
        """
        if not self.enabled:
            return
            
        # Map internal actions to AI-Trader actions
        action_map = {
            'open_long': 'buy',
            'close_short': 'buy', # Covering a short is a buy
            'open_short': 'short',
            'close_long': 'sell'
        }
        
        ai_action = action_map.get(internal_action.lower())
        if not ai_action:
            log.warning(f"⚠️ AITraderClient: Unknown action '{internal_action}', skipping publish.")
            return
            
        # Standardize symbol (e.g., BTCUSDT -> BTC) if needed, but AI-Trader accepts typical crypto symbols.
        clean_symbol = symbol.replace('USDT', '').replace('BUSD', '') if symbol.endswith(('USDT', 'BUSD')) else symbol
            
        payload = {
            "market": "crypto",
            "action": ai_action,
            "symbol": clean_symbol,
            "price": price,
            "quantity": quantity,
            "content": content or f"Automated {internal_action} signal via Multi-Agent AI",
            "executed_at": datetime.utcnow().isoformat() + "Z"
        }
        
        # Dispatch in background thread
        thread = threading.Thread(
            target=self._send_payload, 
            args=(payload,),
            daemon=True
        )
        thread.start()
        
    def _send_payload(self, payload: dict) -> None:
        """Internal method to send the HTTP POST request."""
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(
                f"{self.BASE_URL}/signals/realtime",
                json=payload,
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    log.info(f"📡 AI-Trader: Signal published successfully! (ID: {data.get('signal_id')})")
                else:
                    log.warning(f"⚠️ AI-Trader Publish Failed: {data.get('message', data)}")
            else:
                log.error(f"❌ AI-Trader API Error {response.status_code}: {response.text}")
                
        except Exception as e:
            log.error(f"❌ AI-Trader Client Error: {e}")
