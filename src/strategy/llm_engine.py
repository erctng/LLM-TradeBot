"""
LLM 策略推理引擎 (Multi-Provider Support)
=========================================

支持多种 LLM 提供商: OpenAI, DeepSeek, Claude, Qwen, Gemini, Kimi, MiniMax, GLM
"""
import json
import re
from typing import Dict, Optional
import os
import httpx
from src.config import config
from src.utils.logger import log
from src.utils.action_protocol import VALID_ACTIONS
from src.strategy.llm_parser import LLMOutputParser
from src.strategy.decision_validator import DecisionValidator
from src.llm import create_client, LLMConfig


def _extract_json_robust(text: str) -> Optional[Dict]:
    """
    Robustly extract JSON from LLM response text.
    
    Tries multiple patterns to handle various LLM output formats:
    1. Markdown code block with ```json
    2. Raw JSON object
    3. Nested JSON with proper brace matching
    """
    if not text:
        return None
    
    # Pattern 1: Markdown code block
    md_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if md_match:
        try:
            return json.loads(md_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Pattern 2: Find balanced JSON object
    # Find first { and match to its closing }
    start = text.find('{')
    if start != -1:
        depth = 0
        for i, char in enumerate(text[start:], start):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        break
    
    return None


class StrategyEngine:
    """多 LLM 提供商策略决策引擎"""
    
    def __init__(self):
        # 获取 LLM 配置
        llm_config = config.llm
        provider = llm_config.get('provider') or os.getenv('LLM_PROVIDER', 'none')
        self.disable_llm = False
        
        # 获取对应提供商的 API Key
        api_keys = llm_config.get('api_keys', {})
        api_key = api_keys.get(provider)
        
        # 向后兼容: 如果没有新配置，使用旧的 deepseek 配置
        if not api_key and provider == 'deepseek':
            api_key = config.deepseek.get('api_key')
        
        # LLM 参数
        self.provider = provider
        self.model = llm_config.get('model')
        if not self.model and provider == 'deepseek':
            self.model = config.deepseek.get('model', 'deepseek-chat')
        self.temperature = llm_config.get('temperature', config.deepseek.get('temperature', 0.3))
        self.max_tokens = llm_config.get('max_tokens', config.deepseek.get('max_tokens', 2000))
        
        # 初始化解析器和验证器
        self.parser = LLMOutputParser()
        self.validator = DecisionValidator({
            'max_leverage': config.risk.get('max_leverage', 5),
            'max_position_pct': config.risk.get('max_total_position_pct', 30.0),
            'min_risk_reward_ratio': 2.0
        })

        self.client = None
        self.is_ready = False

        disable_env = os.getenv('LLM_DISABLED', '').lower() in ('1', 'true', 'yes', 'on')
        if provider.lower() in ('none', 'disabled', 'off') or disable_env:
            self.disable_llm = True
            log.info("🚫 Strategy Engine LLM disabled by config")
            return
        
        if api_key:
            self._init_client(api_key, llm_config)
            
    def _init_client(self, api_key: str, llm_config: Dict):
        """Initialize LLM Client"""
        llm_cfg = LLMConfig(
            api_key=api_key,
            base_url=llm_config.get('base_url'),
            model=self.model,
            timeout=llm_config.get('timeout', 120),
            max_retries=llm_config.get('max_retries', 3),
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )
        try:
            self.client = create_client(self.provider, llm_cfg)
            self.is_ready = True
            log.info(f"🤖 Strategy Engine initialized (Provider: {self.provider}, Model: {self.model})")
        except Exception as e:
            log.error(f"Failed to create LLM client: {e}")
            self.is_ready = False
    
    def reload_config(self):
        """Reload configuration from global config"""
        # Re-fetch config
        llm_config = config.llm
        provider = llm_config.get('provider') or os.getenv('LLM_PROVIDER', 'none')
        api_keys = llm_config.get('api_keys', {})
        api_key = api_keys.get(provider)
        if not api_key and provider == 'deepseek':
            api_key = config.deepseek.get('api_key')

        disable_env = os.getenv('LLM_DISABLED', '').lower() in ('1', 'true', 'yes', 'on')
        if provider.lower() in ('none', 'disabled', 'off') or disable_env:
            self.disable_llm = True
            self.is_ready = False
            self.client = None
            log.info("🚫 Strategy Engine LLM disabled by config (reload)")
            return False
        
        if api_key:
            self.provider = provider
            self.model = llm_config.get('model')
            if not self.model and provider == 'deepseek':
                self.model = config.deepseek.get('model', 'deepseek-chat')
            self._init_client(api_key, llm_config)
            return True
        return False
    
    def make_decision(self, market_context_text: str, market_context_data: Dict, reflection: str = None, bull_perspective: Dict = None, bear_perspective: Dict = None) -> Dict:
        """
        基于市场上下文做出交易决策
        
        Args:
            market_context_text: 格式化的市场上下文文本
            market_context_data: 原始市场数据
            reflection: 可选的交易反思文本（来自 ReflectionAgent）
            bull_perspective: 可选的多头观点
            bear_perspective: 可选的空头观点
            
        Returns:
            决策结果字典
        """
        if self.disable_llm:
            return self._get_fallback_decision(market_context_data)

        # Ensure client is initialized
        if not self.is_ready:
            if not self.reload_config():
                log.warning("🚫 LLM Strategy Engine not ready (No API Key). Returning fallback.")
                return self._get_fallback_decision(market_context_data)
        
        # 🐂🐻 Get adversarial perspectives if not provided
        if bull_perspective is None:
            log.info("🐂 Gathering Bull perspective (on-demand)...")
            bull_perspective = self.get_bull_perspective(market_context_text)
            
        if bear_perspective is None:
            log.info("🐻 Gathering Bear perspective (on-demand)...")
            bear_perspective = self.get_bear_perspective(market_context_text)
        
        # 🆕 保存Bull/Bear日志 (if they were generated here or passed in)
        try:
            from src.server.state import global_state
            if hasattr(global_state, 'saver') and hasattr(global_state, 'current_cycle_id'):
                global_state.saver.save_bull_bear_perspectives(
                    bull=bull_perspective,
                    bear=bear_perspective,
                    symbol=market_context_data['symbol'],
                    cycle_id=global_state.current_cycle_id
                )
        except Exception as e:
            log.warning(f"Failed to save bull/bear perspectives log: {e}")
        
        system_prompt = self.get_system_prompt()
        user_prompt = self.get_user_prompt(market_context_text, bull_perspective, bear_perspective, reflection)
        
        # 记录 LLM 输入
        log.llm_input(f"正在发送市场数据到 {self.provider}...", market_context_text)

        
        try:
            response = self.client.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            
            # 获取原始响应
            content = response.content
            
            # 使用新解析器解析结构化输出
            parsed = self.parser.parse(content)
            decision = parsed['decision']
            reasoning = parsed['reasoning']
            
            # 标准化 action 字段
            if 'action' in decision:
                decision['action'] = self.parser.normalize_action(
                    decision['action'],
                    position_side=market_context_data.get('position_side')
                )
            
            # 验证决策
            is_valid, errors = self.validator.validate(decision)
            if not is_valid:
                log.warning(f"LLM 决策验证失败: {errors}")
                log.warning(f"原始决策: {decision}")
                return self._get_fallback_decision(market_context_data)
            
            # 记录 LLM 输出
            log.llm_output(f"{self.provider} 返回决策结果", decision)
            if reasoning:
                log.info(f"推理过程:\n{reasoning}")
            
            # 记录决策
            log.llm_decision(
                action=decision.get('action', 'wait'),
                confidence=decision.get('confidence', 0),
                reasoning=decision.get('reasoning', reasoning)
            )
            
            # 添加元数据
            decision['timestamp'] = market_context_data['timestamp']
            decision['symbol'] = market_context_data['symbol']
            decision['model'] = self.model
            decision['raw_response'] = content
            decision['reasoning_detail'] = reasoning
            decision['validation_passed'] = True
            
            # ✅ Return full prompt for logging
            decision['system_prompt'] = system_prompt
            decision['user_prompt'] = user_prompt
            
            # 🐂🐻 Add Bull/Bear perspectives for dashboard
            decision['bull_perspective'] = bull_perspective
            decision['bear_perspective'] = bear_perspective
            
            return decision
            
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code in (401, 402, 403):
                self.disable_llm = True
                self.is_ready = False
                self.client = None
                log.error(f"LLM decision failed: {e} (LLM disabled)")
            else:
                log.error(f"LLM decision failed: {e}")
            # 返回保守决策
            return self._get_fallback_decision(market_context_data)
        except Exception as e:
            log.error(f"LLM decision failed: {e}")
            # 返回保守决策
            return self._get_fallback_decision(market_context_data)
    
    def get_bull_perspective(self, market_context_text: str) -> Dict:
        """
        🐂 Bull Agent: Analyze market from bullish perspective
        
        Args:
            market_context_text: Formatted market context
            
        Returns:
            Dict with bullish_reasons and bull_confidence
        """
        if not self.is_ready or not self.client:
             return {"bullish_reasons": "LLM not ready", "bull_confidence": 50}

        bull_prompt = """You are a BULLISH market analyst. Your job is to find reasons WHY the market could go UP.

Analyze the provided 'Four-Layer Strategy Analysis' and identify:
1. Bullish Trend & Fuel signals (Layer 1)
2. Bullish AI Resonance (Layer 2)
3. Bullish Setup patterns (Layer 3/4)

Output your analysis in this EXACT JSON format:
```json
{
  "stance": "STRONGLY_BULLISH",
  "bullish_reasons": "Your 3-5 key bullish observations, separated by semicolons",
  "bull_confidence": 75
}
```

stance must be one of: STRONGLY_BULLISH, SLIGHTLY_BULLISH, NEUTRAL, UNCERTAIN
bull_confidence should be 0-100 based on how strong the bullish case is.
Focus ONLY on bullish factors. Ignore bearish signals."""

        try:
            response = self.client.chat(
                system_prompt=bull_prompt,
                user_prompt=market_context_text,
                temperature=0.3,
                max_tokens=500
            )
            
            content = response.content
            
            # Parse JSON from response using robust extraction
            result = _extract_json_robust(content)
            if result:
                stance = result.get('stance', 'UNKNOWN')
                log.info(f"🐂 Bull Agent: [{stance}] {result.get('bullish_reasons', '')[:40]}... (Conf: {result.get('bull_confidence', 0)}%)")
                return result
            
            return {"bullish_reasons": "Unable to analyze", "bull_confidence": 50}
            
        except Exception as e:
            log.warning(f"Bull Agent failed: {e}")
            return {"bullish_reasons": "Analysis unavailable", "bull_confidence": 50}
    
    def get_bear_perspective(self, market_context_text: str) -> Dict:
        """
        🐻 Bear Agent: Analyze market from bearish perspective
        
        Args:
            market_context_text: Formatted market context
            
        Returns:
            Dict with bearish_reasons and bear_confidence
        """
        if not self.is_ready or not self.client:
             return {"bearish_reasons": "LLM not ready", "bear_confidence": 50}

        bear_prompt = """You are a BEARISH market analyst. Your job is to find reasons WHY the market could go DOWN.

Analyze the provided 'Four-Layer Strategy Analysis' and identify:
1. Bearish Trend & Fuel signals (Layer 1)
2. Bearish AI Resonance (Layer 2)
3. Bearish Setup patterns (Layer 3/4)

Output your analysis in this EXACT JSON format:
```json
{
  "stance": "STRONGLY_BEARISH",
  "bearish_reasons": "Your 3-5 key bearish observations, separated by semicolons",
  "bear_confidence": 60
}
```

stance must be one of: STRONGLY_BEARISH, SLIGHTLY_BEARISH, NEUTRAL, UNCERTAIN
bear_confidence should be 0-100 based on how strong the bearish case is.
Focus ONLY on bearish factors. Ignore bullish signals."""


        try:
            response = self.client.chat(
                system_prompt=bear_prompt,
                user_prompt=market_context_text,
                temperature=0.3,
                max_tokens=500
            )
            
            content = response.content
            
            # Parse JSON from response using robust extraction
            result = _extract_json_robust(content)
            if result:
                stance = result.get('stance', 'UNKNOWN')
                log.info(f"🐻 Bear Agent: [{stance}] {result.get('bearish_reasons', '')[:40]}... (Conf: {result.get('bear_confidence', 0)}%)")
                return result
            
            return {"bearish_reasons": "Unable to analyze", "bear_confidence": 50}
            
        except Exception as e:
            log.warning(f"Bear Agent failed: {e}")
            return {"bearish_reasons": "Analysis unavailable", "bear_confidence": 50}
    
    def get_system_prompt(self) -> str:
        """Build System Prompt (English Version) or Load Custom via PromptManager"""
        import os
        from src.utils.prompt_manager import PromptManager
        
        def _load_default_engine_prompt() -> str:
            # Check for custom prompt
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            custom_prompt_path = os.path.join(base_dir, 'config', 'custom_prompt.md')
            
            if os.path.exists(custom_prompt_path):
                try:
                    with open(custom_prompt_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if content.strip():
                            log.info("📝 Loading Custom System Prompt from file as fallback default")
                            return content
                except Exception as e:
                    log.error(f"Failed to load custom prompt: {e}")
            
            # Load default from template
            try:
                from src.config.default_prompt_template import DEFAULT_SYSTEM_PROMPT
                return DEFAULT_SYSTEM_PROMPT
            except ImportError:
                log.error("Failed to import DEFAULT_SYSTEM_PROMPT")
                return "Error: Default prompt missing"

        PromptManager.register_default("decision_core", _load_default_engine_prompt)
        return PromptManager.get_prompt("decision_core")
    
    def get_user_prompt(self, market_context: str, bull_perspective: Dict = None, bear_perspective: Dict = None, reflection: str = None) -> str:
        """Build User Prompt - DATA ONLY (No instructions, all rules are in system prompt)"""
        
        # Build adversarial analysis section (data only)
        adversarial_section = ""
        if bull_perspective or bear_perspective:
            bull_reasons = bull_perspective.get('bullish_reasons', 'N/A') if bull_perspective else 'N/A'
            bull_conf = bull_perspective.get('bull_confidence', 50) if bull_perspective else 50
            bull_stance = bull_perspective.get('stance', 'UNKNOWN') if bull_perspective else 'UNKNOWN'
            bear_reasons = bear_perspective.get('bearish_reasons', 'N/A') if bear_perspective else 'N/A'
            bear_conf = bear_perspective.get('bear_confidence', 50) if bear_perspective else 50
            bear_stance = bear_perspective.get('stance', 'UNKNOWN') if bear_perspective else 'UNKNOWN'
            
            adversarial_section = f"""
---
## 🐂🐻 Adversarial Analysis

### 🐂 Bull Agent [{bull_stance}] (Confidence: {bull_conf}%)
{bull_reasons}

### 🐻 Bear Agent [{bear_stance}] (Confidence: {bear_conf}%)
{bear_reasons}
"""
        
        # Build reflection section (data only)
        reflection_section = ""
        if reflection:
            reflection_section = f"""
---
## 🧠 Trading Reflection (Last 10 Trades)

{reflection}
"""
        
        # DATA-ONLY user prompt
        return f"""# 📊 MARKET DATA INPUT

{market_context}
{adversarial_section}{reflection_section}
---

Analyze the above data following the strategy rules in system prompt. Output your decision.
"""
    
    def _get_fallback_decision(self, context: Dict) -> Dict:
        """
        获取兜底决策（当LLM失败时）
        
        返回保守的hold决策
        """
        return {
            'action': 'wait',
            'symbol': context.get('symbol', 'BTCUSDT'),
            'confidence': 0,
            'leverage': 1,
            'position_size_pct': 0,
            'stop_loss_pct': 1.0,
            'take_profit_pct': 2.0,
            'reasoning': 'LLM decision failed, using conservative fallback strategy',
            'timestamp': context.get('timestamp'),
            'is_fallback': True
        }
    
    def validate_decision(self, decision: Dict) -> bool:
        """
        验证决策格式是否正确
        
        Returns:
            True if valid, False otherwise
        """
        required_fields = [
            'action', 'symbol', 'confidence', 'leverage',
            'position_size_pct', 'stop_loss_pct', 'take_profit_pct', 'reasoning'
        ]
        
        # 检查必需字段
        for field in required_fields:
            if field not in decision:
                log.error(f"决策缺少必需字段: {field}")
                return False
        
        # 检查action合法性
        if decision['action'] not in VALID_ACTIONS:
            log.error(f"无效的action: {decision['action']}")
            return False
        
        # 检查数值范围
        if not (0 <= decision['confidence'] <= 100):
            log.error(f"confidence超出范围: {decision['confidence']}")
            return False
        
        # STRICT ENFORCEMENT: Open trades must meet Dynamic Confidence Threshold
        # OPTIMIZATION (Phase 5): Regime-Based Dynamic Thresholds
        # - Strong Trend: 60% (Aggressive)
        # - Weak Trend: 70% (Standard)
        # - Choppy/Volatile: 80% (Conservative) or NO TRADE
        
        action = decision['action']
        confidence = decision['confidence']
        
        # Extract regime from decision reasoning or context if available
        # Ideally this should be passed in, but we can parse from reasoning or rely on default
        # For now, we set a smart default and rely on the Prompt to guide the confidence score itself.
        # But we can also enforce a hard floor.
        
        regime_threshold = 70 # Default (Weak Trend / Normal)
        
        # Check if reasoning mentions regime (Simple keyword check as fallback)
        reasoning_lower = decision.get('reasoning', '').lower()
        if 'strong trend' in reasoning_lower or 'strong_trend' in reasoning_lower:
            regime_threshold = 60
        elif 'choppy' in reasoning_lower:
            regime_threshold = 75
        elif 'volatile' in reasoning_lower:
             regime_threshold = 70
            
        if action in ['open_long', 'open_short'] and confidence < regime_threshold:
            log.warning(f"🚫 Confidence {confidence}% < Threshold {regime_threshold}% for {action}, converting to 'wait'")
            decision['action'] = 'wait'
            decision['reasoning'] = f"Low confidence ({confidence}% < {regime_threshold}% dynamic threshold), wait for better setup"
        
        if not (1 <= decision['leverage'] <= config.risk.get('max_leverage', 5)):
            log.error(f"leverage超出范围: {decision['leverage']}")
            return False
        
        if not (0 <= decision['position_size_pct'] <= 100):
            log.error(f"position_size_pct超出范围: {decision['position_size_pct']}")
            return False
        
        return True
