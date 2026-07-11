import os
import json
import logging
from typing import Dict, List, Optional
from src.utils.llm_client import create_client, LLMConfig
from src.config.config import Config
from src.utils.prompt_manager import PromptManager

log = logging.getLogger(__name__)

class MetaOptimizerAgent:
    """
    Meta-Agent responsible for dynamically updating the prompts of other agents
    based on past trade performance and current market regime.
    """
    def __init__(self, config: Config):
        llm_config = config.llm
        provider = llm_config.get('provider', 'deepseek')
        api_keys = llm_config.get('api_keys', {})
        api_key = api_keys.get(provider)
        
        if not api_key and provider == 'deepseek':
            api_key = config.deepseek.get('api_key')
            
        if not api_key:
            log.warning(f"🔧 MetaOptimizerAgent: No API key for {provider}")
            api_key = "dummy"

        self.client = create_client(provider, LLMConfig(
            api_key=api_key,
            base_url=llm_config.get('base_url'),
            model=llm_config.get('model') or 'deepseek-chat',
            temperature=0.7, # Higher temperature for creative optimization
            max_tokens=2000
        ))
        
        self.min_win_rate_threshold = 0.30 # Rollback if win rate < 30%
        log.info("🧠 MetaOptimizerAgent initialized")

    def evaluate_and_optimize(self, recent_trades: List[Dict], market_regime: str) -> bool:
        """
        Evaluate recent performance and optimize prompts if necessary.
        Returns True if prompts were updated.
        """
        if not recent_trades or len(recent_trades) < 10:
            log.info("🔧 MetaOptimizerAgent: Not enough trades to evaluate.")
            return False

        # Calculate metrics
        wins = [t for t in recent_trades if t.get('profit_pct', 0) > 0]
        win_rate = len(wins) / len(recent_trades)
        total_pnl = sum(t.get('profit_pct', 0) for t in recent_trades)
        
        log.info(f"📊 MetaOptimizer Evaluation | Win Rate: {win_rate:.2%} | Total PnL: {total_pnl:.2f}% | Regime: {market_regime}")

        # Safety Rollback Check
        if win_rate < self.min_win_rate_threshold:
            log.warning(f"🚨 Win Rate ({win_rate:.2%}) below threshold ({self.min_win_rate_threshold:.2%})! Triggering Rollbacks.")
            for agent in ['trend', 'setup', 'trigger', 'decision_core']:
                PromptManager.rollback_prompt(agent)
            return True
            
        # If performance is okay, try to optimize further for current regime
        # Format trade history for LLM
        trades_text = "\n".join([
            f"Trade {i+1}: {t.get('action')} {t.get('symbol')} | PnL: {t.get('profit_pct', 0):.2f}% | Reason: {t.get('reason', 'N/A')}"
            for i, t in enumerate(recent_trades[-30:]) # Last 30 trades max
        ])
        
        active_prompts = PromptManager.get_all_active_prompts()
        
        system_prompt = """You are an elite AI Quant Researcher overseeing a fleet of trading agents.
Your task is to analyze recent trade performance in the current market regime and optimize the system prompts of the trading agents to improve their Sharpe ratio and PnL.
Identify patterns in losing trades and adjust the rules in the prompts to prevent them.
If winning trades are being cut short, adjust the take-profit guidance.

Output MUST be a valid JSON object matching this schema exactly:
{
  "reasoning": "Explanation of what you are changing and why.",
  "prompts": {
    "trend": "new complete prompt for TrendAgent",
    "setup": "new complete prompt for SetupAgent",
    "trigger": "new complete prompt for TriggerAgent",
    "decision_core": "new complete prompt for DecisionCore"
  }
}

You must return ALL FOUR prompts, fully written out, incorporating your new rules into their existing structure. DO NOT use markdown code blocks like ```json around the output. Output pure JSON."""

        user_prompt = f"""
### Current Market Regime
{market_regime}

### Recent Performance
Win Rate: {win_rate:.2%}
Total PnL: {total_pnl:.2f}%

### Recent Trades
{trades_text}

### Current Active Prompts
```json
{json.dumps(active_prompts, indent=2)}
```

Generate the optimized JSON now.
"""
        try:
            log.info("🧠 Asking LLM to optimize agent prompts...")
            response = self.client.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            
            # Clean up response if it contains markdown blocks
            clean_response = response.strip()
            if clean_response.startswith("```json"):
                clean_response = clean_response[7:]
            if clean_response.startswith("```"):
                clean_response = clean_response[3:]
            if clean_response.endswith("```"):
                clean_response = clean_response[:-3]
                
            optimized_data = json.loads(clean_response)
            
            reasoning = optimized_data.get('reasoning', 'No reasoning provided.')
            log.info(f"✨ LLM Optimization Reasoning: {reasoning}")
            
            new_prompts = optimized_data.get('prompts', {})
            updated = False
            
            for agent_name, new_prompt in new_prompts.items():
                if agent_name in ['trend', 'setup', 'trigger', 'decision_core'] and new_prompt:
                    if new_prompt != active_prompts.get(agent_name):
                        success = PromptManager.update_prompt(agent_name, new_prompt, reason=f"Market Regime: {market_regime}. {reasoning[:100]}...")
                        if success:
                            updated = True
                            
            return updated
            
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse optimizer JSON: {e}")
            log.debug(f"Raw output: {response}")
            return False
        except Exception as e:
            log.error(f"MetaOptimizer Agent failed: {e}")
            return False
