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

    def sync_copy_trades(self, execution_callback) -> None:
        """
        Poll AI-Trader platform for copied positions and sync state locally.
        Should be called periodically (e.g., every 30s) in a background thread.
        
        Args:
            execution_callback: Function to call to execute a trade locally.
                                Signature: callback(action: str, symbol: str, quantity: float, reason: str)
        """
        if not self.enabled:
            return
            
        from src.server.state import global_state
        
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(
                f"{self.BASE_URL}/positions",
                headers=headers,
                timeout=10
            )
            
            if response.status_code != 200:
                log.warning(f"⚠️ AI-Trader Sync Failed: HTTP {response.status_code}")
                return
                
            data = response.json()
            remote_positions = data.get("positions", [])
            
            # Filter only copied positions
            copied_remote = {}
            for pos in remote_positions:
                source = pos.get("source", "")
                if source.startswith("copied:"):
                    # Treat AI-Trader symbol (e.g., BTC) as Binance symbol (e.g., BTCUSDT)
                    symbol = pos.get("symbol", "")
                    if "USDT" not in symbol and "BUSD" not in symbol:
                        symbol += "USDT"
                    copied_remote[symbol] = pos
            
            # Compare with local state
            with global_state.locked():
                local_copied = global_state.copied_positions
                
                # Check for NEW copied positions (open)
                for symbol, pos in copied_remote.items():
                    if symbol not in local_copied:
                        log.info(f"🔄 CopyTrade: Detected new position for {symbol} from {pos.get('source')}")
                        # In AI-Trader, quantity is > 0 for Long, < 0 for Short ? 
                        # Actually, if side isn't provided, let's assume it's a LONG for now or infer from quantity.
                        # Wait, the AI-Trader doc shows: { "symbol": "BTC", "quantity": 0.5, "entry_price": 50000, "current_price": 51000, "pnl": 500, "source": "copied:10" }
                        # Let's assume quantity > 0 means Long, < 0 means Short, OR it only supports Longs for now.
                        action = "open_long" if pos.get("quantity", 0) > 0 else "open_short"
                        
                        # We use the default bot quantity rules if we don't pass a specific quantity here,
                        # but execution_callback requires it. We'll pass 0.0 to let the ExecutionEngine calculate it based on risk.
                        execution_callback(
                            action=action,
                            symbol=symbol,
                            quantity=0.0, # 0.0 means 'use default risk amount' (Option C)
                            reason=f"CopyTrade {pos.get('source')}"
                        )
                        # Add to local state
                        global_state.copied_positions[symbol] = pos
                
                # Check for CLOSED copied positions (close)
                symbols_to_remove = []
                for symbol, pos in local_copied.items():
                    if symbol not in copied_remote:
                        log.info(f"🔄 CopyTrade: Detected closed position for {symbol} from {pos.get('source')}")
                        action = "close_long" if pos.get("quantity", 0) > 0 else "close_short"
                        
                        execution_callback(
                            action=action,
                            symbol=symbol,
                            quantity=0.0, # Close entirely
                            reason=f"CopyTrade Closed {pos.get('source')}"
                        )
                        symbols_to_remove.append(symbol)
                        
                for symbol in symbols_to_remove:
                    del global_state.copied_positions[symbol]
                    
        except Exception as e:
            log.error(f"❌ AI-Trader CopyTrade Sync Error: {e}")

    def auto_follow_best_trader(self) -> None:
        """
        Scan AI-Trader leaderboard and subscribe to the most profitable trader.
        Unfollows any other previously followed traders to free up capital (Option A).
        """
        if not self.enabled:
            return
            
        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            # 1. Fetch Leaderboard (Grouped Signals)
            resp_grouped = requests.get(f"{self.BASE_URL}/signals/grouped?limit=50", headers=headers, timeout=10)
            if resp_grouped.status_code != 200:
                log.warning(f"⚠️ Auto-Follow failed to fetch leaderboard: HTTP {resp_grouped.status_code}")
                return
            
            agents = resp_grouped.json().get("agents", [])
            if not agents:
                return
                
            # Find the best agent by total_pnl > 0
            best_agent = None
            max_pnl = 0.0
            for agent in agents:
                pnl = float(agent.get("total_pnl", 0.0))
                if pnl > max_pnl:
                    max_pnl = pnl
                    best_agent = agent
                    
            if not best_agent:
                log.info("No profitable leader found to auto-follow right now.")
                return
                
            target_leader_id = best_agent["agent_id"]
            target_leader_name = best_agent["agent_name"]
            
            # 2. Check current subscriptions
            resp_following = requests.get(f"{self.BASE_URL}/signals/following", headers=headers, timeout=10)
            if resp_following.status_code != 200:
                return
                
            subscriptions = resp_following.json().get("subscriptions", [])
            already_following_best = False
            leaders_to_unfollow = []
            
            for sub in subscriptions:
                if sub.get("status") == "active":
                    sub_id = sub.get("leader_id")
                    if sub_id == target_leader_id:
                        already_following_best = True
                    else:
                        leaders_to_unfollow.append((sub_id, sub.get("leader_name")))
                        
            # 3. Unfollow previous leaders
            for uid, uname in leaders_to_unfollow:
                log.info(f"🔄 Auto-Follow: Unfollowing previous leader {uname} (ID: {uid}) to switch to #1.")
                requests.post(f"{self.BASE_URL}/signals/unfollow", headers=headers, json={"leader_id": uid}, timeout=10)
                
            # 4. Follow best leader if not already following
            if not already_following_best:
                log.info(f"🏆 Auto-Follow: Subscribing to new #1 leader {target_leader_name} (PnL: +{max_pnl:.2f})!")
                requests.post(f"{self.BASE_URL}/signals/follow", headers=headers, json={"leader_id": target_leader_id}, timeout=10)
                
        except Exception as e:
            log.error(f"❌ Auto-Follow Error: {e}")
