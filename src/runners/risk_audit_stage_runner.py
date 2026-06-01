import asyncio

from typing import Dict, Optional, Any, Tuple, List

from src.config import Config

from src.api.binance_client import BinanceClient
from src.agents.agent_config import AgentConfig
from src.agents.runtime_events import emit_global_runtime_event
from src.utils.helper import get_current_position  # ✅ Global Import

from src.utils.logger import log
from src.server.state import global_state
from src.utils.agents_util import get_agent_timeout

from src.trading import CycleContext

from src.agents import (
    AgentProvider,
    PositionInfo
)
from src.utils.action_protocol import (
    normalize_action,
    is_close_action,
)

from .runner_decorators import log_run

class RiskAuditStageRunner:
    def __init__(
        self,
        config: Config,
        agent_config: AgentConfig,
        client: BinanceClient,
        agent_provider: AgentProvider,
        leverage: int,
        stop_loss_pct: float,
        take_profit_pct: float,
        test_mode: bool = False
    ):
        self.config = config
        self.agent_config = agent_config
        self.client = client
        self.agent_provider = agent_provider
        self.leverage = leverage
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.test_mode = test_mode
    
    @log_run
    async def run(
        self,
        context: CycleContext
    ) -> Tuple[Dict[str, Any], Any, float, Optional[PositionInfo]]:
        """Run guardian audit stage and return order params + audit result."""
        emit_global_runtime_event(
            context,
            stream="lifecycle",
            agent="risk_audit",
            phase="start",
        )

        regime_txt = context.vote_result.regime.get('regime', 'Unknown') if context.vote_result.regime else 'Unknown'
        global_state.add_log(
            f"⚖️ DecisionCoreAgent (The Critic): Context(Regime={regime_txt}) => "
            f"Vote: {context.vote_result.action.upper()} (Conf: {context.vote_result.confidence:.0f}%)"
        )
        global_state.guardian_status = "Auditing..."

        order_params = self._build_risk_order_params(context)

        print(f"  ✅ 信号方向: {context.vote_result.action}")
        print(f"  ✅ 综合信心: {context.vote_result.confidence:.1f}%")
        if context.vote_result.regime:
            print(f"  📊 市场状态: {context.vote_result.regime['regime']}")
        if context.vote_result.position:
            print(
                f"  📍 价格位置: {min(max(context.vote_result.position['position_pct'], 0), 100):.1f}% "
                f"({context.vote_result.position['location']})"
            )

        account_balance = self._refresh_account_state_for_audit()
        current_position =  get_current_position(self.client, context.symbol, self.test_mode)
        atr_pct = context.regime_result.get('atr_pct', None) if context.regime_result else None

        global_state.add_agent_message("risk_audit", "🛡️ Guardian is auditing risk and positions...", level="info")
        from src.agents.risk_audit_agent import RiskCheckResult, RiskLevel
        fallback_audit = RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.FATAL,
            blocked_reason="risk_audit_unavailable",
            corrections=None,
            warnings=["Risk audit degraded, decision blocked by safety fallback"]
        )
        risk_timeout = get_agent_timeout(self.config, self.agent_config,'risk_audit', 20.0)
        try:
            audit_result = await asyncio.wait_for(
                self.agent_provider.risk_audit_agent.audit_decision(
                    decision=order_params,
                    current_position=current_position,
                    account_balance=account_balance,
                    current_price=context.current_price,
                    atr_pct=atr_pct
                ),
                timeout=risk_timeout
            )
        except asyncio.TimeoutError:
            log.warning(f"⏱️ risk_audit timeout after {risk_timeout:.1f}s, blocking decision by fallback")
            global_state.add_agent_message(
                "risk_audit",
                f"TIMEOUT | audit>{risk_timeout:.1f}s, blocked by safety policy",
                level="warning"
            )
            emit_global_runtime_event(
                context,
                stream="error",
                agent="risk_audit",
                phase="timeout",
                data={"timeout_seconds": risk_timeout}
            )
            audit_result = fallback_audit
        except Exception as e:
            emit_global_runtime_event(
                context,
                stream="error",
                agent="risk_audit",
                phase="error",
                data={"error": str(e)}
            )
            log.error(f"❌ risk_audit failed, blocking decision by fallback: {e}")
            audit_result = fallback_audit

        emit_global_runtime_event(
            context,
            stream="lifecycle",
            agent="risk_audit",
            phase="end",
            data={
                "passed": audit_result.passed,
                "risk_level": audit_result.risk_level.value
            }
        )

        global_state.guardian_status = "PASSED" if audit_result.passed else "BLOCKED"
        if not audit_result.passed:
            global_state.add_log(f"[🛡️ GUARDIAN] ❌ BLOCKED ({audit_result.blocked_reason})")
            global_state.add_agent_message(
                "risk_audit",
                f"BLOCKED | {audit_result.blocked_reason}",
                level="warning"
            )
        else:
            global_state.add_log(f"[🛡️ GUARDIAN] ✅ PASSED (Risk: {audit_result.risk_level.value})")
            global_state.add_agent_message(
                "risk_audit",
                f"PASSED | Risk: {audit_result.risk_level.value}",
                level="success"
            )

        return order_params, audit_result, account_balance, current_position

    def _build_risk_order_params(
        self,
        context: CycleContext
    ) -> Dict[str, Any]:
        """Build risk-audit input payload from decision + market context."""
        order_params = self._build_order_params(
            symbol=context.symbol,
            action=context.vote_result.action,
            current_price=context.current_price,
            confidence=context.vote_result.confidence,
            position_info=context.current_position_info
        )
        order_params['symbol'] = context.symbol
        order_params['regime'] = context.vote_result.regime
        order_params['position'] = context.vote_result.position
        order_params['confidence'] = context.vote_result.confidence

        osc_data = context.quant_analysis.get('oscillator', {}) if isinstance(context.quant_analysis, dict) else {}
        order_params['oscillator_scores'] = {
            'osc_1h_score': osc_data.get('osc_1h_score', 0),
            'osc_15m_score': osc_data.get('osc_15m_score', 0),
            'osc_5m_score': osc_data.get('osc_5m_score', 0)
        }
        sentiment_data = context.quant_analysis.get('sentiment', {}) if isinstance(context.quant_analysis, dict) else {}
        order_params['sentiment_score'] = sentiment_data.get('total_sentiment_score', 0)
        order_params.update(self._get_symbol_trade_stats(context.symbol))
        trend_data = context.quant_analysis.get('trend', {}) if isinstance(context.quant_analysis, dict) else {}
        order_params['trend_scores'] = {
            'trend_1h_score': trend_data.get('trend_1h_score', 0),
            'trend_15m_score': trend_data.get('trend_15m_score', 0),
            'trend_5m_score': trend_data.get('trend_5m_score', 0)
        }
        four_layer = getattr(global_state, 'four_layer_result', {}) or {}
        if isinstance(four_layer, dict):
            order_params['four_layer'] = {
                'layer1_pass': bool(four_layer.get('layer1_pass')),
                'layer2_pass': bool(four_layer.get('layer2_pass')),
                'layer3_pass': bool(four_layer.get('layer3_pass')),
                'layer4_pass': bool(four_layer.get('layer4_pass')),
                'final_action': four_layer.get('final_action', 'wait'),
                'trigger_pattern': four_layer.get('trigger_pattern'),
                'setup_quality': four_layer.get('setup_quality'),
                'setup_override': four_layer.get('setup_override'),
                'trend_continuation_mode': bool(four_layer.get('trend_continuation_mode')),
                'adx': four_layer.get('adx'),
                'oi_change': four_layer.get('oi_change'),
                'trigger_rvol': four_layer.get('trigger_rvol'),
            }

        try:
            if self.agent_config.position_analyzer_agent:
                from src.agents.position_analyzer_agent import PositionAnalyzer
                df_1h = context.processed_dfs.get('1h')
                if df_1h is not None and len(df_1h) > 5:
                    analyzer = PositionAnalyzer()
                    order_params['position_1h'] = analyzer.analyze_position(
                        df_1h,
                        context.current_price,
                        timeframe='1h'
                    )
        except Exception:
            pass

        return order_params

    def _get_symbol_trade_stats(self, symbol: str, max_trades: int = 5) -> Dict:
        """Summarize recent closed trades for symbol to support risk filters."""
        history = global_state.trade_history or []
        closed_pnls: List[float] = []
        long_pnls: List[float] = []
        short_pnls: List[float] = []

        for trade in history:
            if trade.get('symbol') != symbol:
                continue

            pnl = trade.get('pnl')
            if pnl is None:
                continue

            status = str(trade.get('status', '')).upper()
            close_cycle = trade.get('close_cycle', 0)
            exit_price = trade.get('exit_price', 0)
            is_closed = (
                'CLOSED' in status or
                (isinstance(close_cycle, (int, float)) and close_cycle > 0) or
                (isinstance(exit_price, (int, float)) and exit_price > 0)
            )
            if not is_closed:
                continue

            try:
                pnl_value = float(pnl)
            except Exception:
                continue

            closed_pnls.append(pnl_value)
            normalized_action = normalize_action(str(trade.get('action', '')).lower())
            if normalized_action == 'open_long':
                long_pnls.append(pnl_value)
            elif normalized_action == 'open_short':
                short_pnls.append(pnl_value)

        def _calc_bucket_stats(pnls: List[float]) -> Tuple[int, float, int, Optional[float]]:
            loss_streak = 0
            for value in pnls:
                if value < 0:
                    loss_streak += 1
                else:
                    break

            recent = pnls[:max_trades]
            recent_count = len(recent)
            recent_pnl = float(sum(recent)) if recent else 0.0
            wins = sum(1 for value in recent if value > 0)
            win_rate = (wins / recent_count) if recent_count > 0 else None
            return loss_streak, recent_pnl, recent_count, win_rate

        loss_streak, recent_pnl, recent_count, win_rate = _calc_bucket_stats(closed_pnls)
        long_loss_streak, long_recent_pnl, long_recent_count, long_win_rate = _calc_bucket_stats(long_pnls)
        short_loss_streak, short_recent_pnl, short_recent_count, short_win_rate = _calc_bucket_stats(short_pnls)

        return {
            'symbol_loss_streak': loss_streak,
            'symbol_recent_pnl': recent_pnl,
            'symbol_recent_trades': recent_count,
            'symbol_win_rate': win_rate,
            'symbol_long_loss_streak': long_loss_streak,
            'symbol_long_recent_pnl': long_recent_pnl,
            'symbol_long_recent_trades': long_recent_count,
            'symbol_long_win_rate': long_win_rate,
            'symbol_short_loss_streak': short_loss_streak,
            'symbol_short_recent_pnl': short_recent_pnl,
            'symbol_short_recent_trades': short_recent_count,
            'symbol_short_win_rate': short_win_rate,
        }

    def _refresh_account_state_for_audit(self) -> float:
        """Fetch account state and sync dashboard fields before risk audit."""
        try:
            if self.test_mode:
                wallet_bal = global_state.virtual_balance
                avail_bal = global_state.virtual_balance
                unrealized_pnl = sum(
                    pos.get('unrealized_pnl', 0)
                    for pos in global_state.virtual_positions.values()
                )
                total_equity = wallet_bal + unrealized_pnl
                initial_balance = global_state.virtual_initial_balance
                total_pnl = total_equity - initial_balance

                global_state.update_account(
                    equity=total_equity,
                    available=avail_bal,
                    wallet=wallet_bal,
                    pnl=total_pnl
                )
                global_state.record_account_success()
                return float(avail_bal)

            acc_info = self.client.get_futures_account()
            wallet_bal = float(acc_info.get('total_wallet_balance', 0))
            unrealized_pnl = float(acc_info.get('total_unrealized_profit', 0))
            avail_bal = float(acc_info.get('available_balance', 0))
            total_equity = wallet_bal + unrealized_pnl

            global_state.update_account(
                equity=total_equity,
                available=avail_bal,
                wallet=wallet_bal,
                pnl=unrealized_pnl
            )
            global_state.record_account_success()
            return avail_bal
        except Exception as e:
            log.error(f"Failed to fetch account info: {e}")
            global_state.record_account_failure()
            global_state.add_log(f"❌ Account info fetch failed: {str(e)}")
            return 0.0
        
    def _build_order_params(
        self,
        symbol: str,
        action: str, 
        current_price: float,
        confidence: float,
        position_info: Optional[Dict] = None
    ) -> Dict:
        """
        构建订单参数
        
        Args:
            action: trading action
            current_price: 当前价格
            confidence: 决策置信度 (0-100)
        
        Returns:
            订单参数字典
        """
        action = normalize_action(action, position_side=(position_info or {}).get('side'))

        # 获取可用余额
        if self.test_mode:
            available_balance = global_state.virtual_balance
        else:
            available_balance = self.client.get_account_balance()
        
        # 动态仓位计算：置信度 100% 时使用可用余额的 33%
        # 公式: 仓位比例 = 基础比例(33%) × 置信度
        base_position_pct = 1 / 3  # 最大仓位比例 33%
        conf_pct = confidence
        if isinstance(conf_pct, (int, float)) and 0 < conf_pct <= 1:
            conf_pct *= 100
        conf_pct = max(0.0, min(float(conf_pct or 0.0), 100.0))
        position_pct = base_position_pct * (conf_pct / 100)  # 根据置信度调整
        
        # 计算仓位金额（完全基于可用余额百分比）
        adjusted_position = available_balance * position_pct
        
        # 计算数量
        quantity = adjusted_position / current_price if current_price > 0 else 0.0
        if is_close_action(action):
            if position_info and isinstance(position_info.get('quantity'), (int, float)):
                quantity = float(position_info.get('quantity', 0) or 0)
            elif self.test_mode:
                pos = (global_state.virtual_positions or {}).get(symbol, {})
                quantity = float(pos.get('quantity', 0) or 0)
        
        # 计算止损止盈
        if action == 'open_long':
            stop_loss = current_price * (1 - self.stop_loss_pct / 100)
            take_profit = current_price * (1 + self.take_profit_pct / 100)
        elif action == 'open_short':
            stop_loss = current_price * (1 + self.stop_loss_pct / 100)
            take_profit = current_price * (1 - self.take_profit_pct / 100)
        else:
            stop_loss = current_price
            take_profit = current_price
        
        return {
            'action': action,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'quantity': quantity,
            'position_value': adjusted_position,  # 新增：实际仓位金额
            'position_pct': position_pct * 100,   # 新增：仓位百分比
            'leverage': self.leverage,
            'confidence': confidence
        }

