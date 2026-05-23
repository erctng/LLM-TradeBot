from datetime import datetime
from typing import Dict, Optional, Any

from src.utils.data_saver import DataSaver
from src.agents.runtime_events import emit_global_runtime_event
from src.utils.helper import get_position_1h_veto_reason

from src.utils.logger import log
from src.utils.trade_logger import trade_logger
from src.server.state import global_state

from src.trading import CycleContext

from src.utils.action_protocol import (
    normalize_action,
    is_open_action,
    is_close_action
)

from .runner_decorators import log_run

class ExecutionStageRunner:
    def __init__(
        self,
        saver: DataSaver,
        test_mode: bool,
        execute_order_callback=None,
        save_virtual_state_callback=None
    ):
        self.saver = saver
        self.test_mode = test_mode
        self.execute_order_callback = execute_order_callback
        self.save_virtual_state_callback = save_virtual_state_callback

    def _save_virtual_state(self):
        if self.save_virtual_state_callback:
            self.save_virtual_state_callback()
        else:
            self.saver.save_virtual_account(global_state.virtual_balance, global_state.virtual_positions)

    def _execute_order(self, order_params: Dict) -> bool:
        if self.execute_order_callback:
            return self.execute_order_callback(order_params)
        log.error("No execute_order_callback provided to ExecutionStageRunner")
        return False

    @log_run
    async def run(
        self,
        context: CycleContext,
        headless_mode: bool
    ) -> Dict[str, Any]:
        """Run order execution stage (test/live) with unified lifecycle events."""
        veto_reason = get_position_1h_veto_reason(context.order_params)
        if veto_reason:
            global_state.add_log(f"[🛡️ EXECUTION_VETO] {veto_reason}")
            return {
                'status': 'blocked',
                'action': context.order_params.get('action', context.vote_result.action),
                'details': {'reason': veto_reason, 'stage': 'execution_gate'},
                'current_price': context.current_price
            }
        
        emit_global_runtime_event(
            context,
            stream="lifecycle",
            agent="executor",
            phase="start",
            data={"mode": "test" if self.test_mode else "live"}
        )

        if self.test_mode:
            if not headless_mode:
                print("\n[Step 5/5] 🧪 TestMode - 模拟执行...")
            return self._execute_test_mode_order(context)

        if not headless_mode:
            print("\n[Step 5/5] 🚀 LiveTrade - 实盘执行...")
        return self._execute_live_mode_order(context)

    def _execute_test_mode_order(
        self,
        context: CycleContext
    ) -> Dict[str, Any]:
        """Execute simulated order path for test mode."""
        print(f"  模拟订单: {context.order_params['action']} {context.order_params['quantity']} @ {context.current_price}")
        global_state.add_log(f"[🚀 EXECUTOR] Test: {context.order_params['action'].upper()} {context.order_params['quantity']} @ {context.current_price:.2f}")

        self.saver.save_execution({
            'symbol': context.symbol,
            'action': 'SIMULATED_EXECUTION',
            'params': context.order_params,
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'cycle_id': context.cycle_id
        }, context.symbol, cycle_id=context.cycle_id)

        realized_pnl = 0.0
        exit_test_price = 0.0
        normalized_action = normalize_action(
            context.vote_result.action,
            position_side=(context.current_position_info or {}).get('side')
        )

        if is_close_action(normalized_action):
            if context.symbol in global_state.virtual_positions:
                pos = global_state.virtual_positions[context.symbol]
                entry_price = pos['entry_price']
                qty = pos['quantity']
                side = pos['side']

                if side.upper() == 'LONG':
                    realized_pnl = (context.current_price - entry_price) * qty
                else:
                    realized_pnl = (entry_price - context.current_price) * qty

                exit_test_price = context.current_price
                global_state.virtual_balance += realized_pnl
                del global_state.virtual_positions[context.symbol]
                self._save_virtual_state()
                log.info(f"💰 [TEST] Closed {side} {context.symbol}: PnL=${realized_pnl:.2f}, Bal=${global_state.virtual_balance:.2f}")
            else:
                log.warning(f"⚠️ [TEST] Close ignored - No position for {context.symbol}")
        elif is_open_action(normalized_action):
            side = 'LONG' if normalized_action == 'open_long' else 'SHORT'
            position_value = context.order_params['quantity'] * context.current_price
            global_state.virtual_positions[context.symbol] = {
                'entry_price': context.current_price,
                'quantity': context.order_params['quantity'],
                'side': side,
                'entry_time': datetime.now().isoformat(),
                'stop_loss': context.order_params.get('stop_loss_price', 0),
                'take_profit': context.order_params.get('take_profit_price', 0),
                'leverage': context.order_params.get('leverage', 1),
                'position_value': position_value
            }
            self._save_virtual_state()
            log.info(f"💰 [TEST] Opened {side} {context.symbol} @ ${context.current_price:,.2f}")

        is_close_trade_action = is_close_action(context.vote_result.action)
        self._persist_trade_history(
            symbol=context.symbol,
            order_params=context.order_params,
            cycle_id=context.cycle_id,
            entry_price=context.current_price,
            exit_price=exit_test_price,
            pnl=realized_pnl,
            is_close_trade_action=is_close_trade_action,
            open_status='SIMULATED',
            entry_field='entry_price',
            include_timestamp=True
        )

        if is_open_action(context.vote_result.action):
            global_state.cycle_positions_opened += 1
            log.info(f"Positions opened this cycle: {global_state.cycle_positions_opened}/1")

        emit_global_runtime_event(
            context,
            stream="lifecycle",
            agent="executor",
            phase="end",
            data={"status": "success", "mode": "test", "action": context.vote_result.action}
        )
        return {
            'status': 'success',
            'action': context.vote_result.action,
            'details': context.order_params,
            'current_price': context.current_price
        }

    def _execute_live_mode_order(
        self,
        context: CycleContext
    ) -> Dict[str, Any]:
        """Execute live order path."""
        try:
            is_success = self._execute_order(context.order_params)
            status_icon = "✅" if is_success else "❌"
            status_txt = "SENT" if is_success else "FAILED"
            global_state.add_log(f"[🚀 EXECUTOR] Live: {context.order_params['action'].upper()} {context.order_params['quantity']} => {status_icon} {status_txt}")
            executed = {'status': 'filled' if is_success else 'failed', 'avgPrice': context.current_price, 'executedQty': context.order_params['quantity']}
        except Exception as e:
            log.error(f"Live order execution failed: {e}", exc_info=True)
            global_state.add_log(f"[Execution] ❌ Live Order Failed: {e}")
            emit_global_runtime_event(
                context,
                stream="error",
                agent="executor",
                phase="error",
                data={"status": "failed", "mode": "live", "error": str(e)}
            )
            return {
                'status': 'failed',
                'action': context.vote_result.action,
                'details': {'error': str(e)}
            }

        self.saver.save_execution({
            'symbol': context.symbol,
            'action': 'REAL_EXECUTION',
            'params': context.order_params,
            'status': 'success' if executed else 'failed',
            'timestamp': datetime.now().isoformat(),
            'cycle_id': context.cycle_id
        }, context.symbol, cycle_id=context.cycle_id)

        if executed:
            print("  ✅ 订单执行成功!")
            log_price = context.order_params.get('entry_price', context.current_price)
            global_state.add_log(f"✅ Order: {context.order_params['action'].upper()} {context.order_params['quantity']} @ ${log_price}")

            trade_logger.log_open_position(
                symbol=context.symbol,
                side=context.order_params['action'].upper(),
                decision=context.order_params,
                execution_result={
                    'success': True,
                    'entry_price': context.order_params['entry_price'],
                    'quantity': context.order_params['quantity'],
                    'stop_loss': context.order_params['stop_loss'],
                    'take_profit': context.order_params['take_profit'],
                    'order_id': 'real_order'
                },
                market_state=context.market_snapshot.live_5m,
                account_info={'available_balance': context.account_balance}
            )

            pnl = 0.0
            exit_price = 0.0
            entry_price = context.order_params['entry_price']
            if is_close_action(context.order_params.get('action')) and context.current_position:
                exit_price = context.current_price
                entry_price = context.current_position.entry_price
                direction = 1 if context.current_position.side == 'long' else -1
                pnl = (exit_price - entry_price) * context.current_position.quantity * direction

            is_close_trade_action = is_close_action(context.order_params.get('action'))
            self._persist_trade_history(
                symbol=context.symbol,
                order_params=context.order_params,
                cycle_id=context.cycle_id,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                is_close_trade_action=is_close_trade_action,
                open_status='EXECUTED',
                entry_field='price',
                include_timestamp=False
            )

            emit_global_runtime_event(
                context,
                stream="lifecycle",
                agent="executor",
                phase="end",
                data={"status": "success", "mode": "live", "action": context.vote_result.action}
            )
            return {
                'status': 'success',
                'action': context.vote_result.action,
                'details': context.order_params,
                'current_price': context.current_price
            }

        print("  ❌ 订单执行失败")
        global_state.add_log(f"❌ Order Failed: {context.order_params['action'].upper()}")
        emit_global_runtime_event(
            context,
            stream="error",
            agent="executor",
            phase="error",
            data={"status": "failed", "mode": "live", "error": "execution_failed"}
        )
        return {
            'status': 'failed',
            'action': context.vote_result.action,
            'details': {'error': 'execution_failed'},
            'current_price': context.current_price
        }
    
    def _persist_trade_history(
        self,
        *,
        symbol: str,
        order_params: Dict[str, Any],
        cycle_id: Optional[str],
        entry_price: float,
        exit_price: float,
        pnl: float,
        is_close_trade_action: bool,
        open_status: str,
        entry_field: str,
        include_timestamp: bool
    ) -> bool:
        """Persist/merge trade record to storage + in-memory history."""
        update_success = False
        if is_close_trade_action:
            update_success = self.saver.update_trade_exit(
                symbol=symbol,
                exit_price=exit_price,
                pnl=pnl,
                exit_time=datetime.now().strftime("%H:%M:%S"),
                close_cycle=global_state.cycle_counter
            )
            if update_success:
                for trade in global_state.trade_history:
                    if trade.get('symbol') == symbol and trade.get('exit_price', 0) == 0:
                        trade['exit_price'] = exit_price
                        trade['pnl'] = pnl
                        trade['close_cycle'] = global_state.cycle_counter
                        trade['status'] = 'CLOSED'
                        log.info(f"✅ Synced global_state.trade_history: {symbol} PnL ${pnl:.2f}")
                        break
                global_state.cumulative_realized_pnl += pnl
                log.info(f"📊 Cumulative Realized PnL: ${global_state.cumulative_realized_pnl:.2f}")

        if not update_success:
            is_open_trade_action = is_open_action(order_params.get('action'))
            original_open_cycle = 0
            if not is_open_trade_action:
                for trade in global_state.trade_history:
                    if trade.get('symbol') == symbol and trade.get('exit_price', 0) == 0:
                        original_open_cycle = trade.get('open_cycle', 0)
                        break

            trade_record = {
                'open_cycle': global_state.cycle_counter if is_open_trade_action else original_open_cycle,
                'close_cycle': 0 if is_open_trade_action else global_state.cycle_counter,
                'action': order_params['action'].upper(),
                'symbol': symbol,
                entry_field: entry_price,
                'quantity': order_params['quantity'],
                'cost': entry_price * order_params['quantity'],
                'exit_price': exit_price,
                'pnl': pnl,
                'confidence': order_params['confidence'],
                'status': open_status,
                'cycle': cycle_id
            }
            if include_timestamp:
                trade_record['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if is_close_trade_action:
                trade_record['status'] = 'CLOSED (Fallback)'

            self.saver.save_trade(trade_record)
            global_state.trade_history.insert(0, trade_record)
            if len(global_state.trade_history) > 50:
                global_state.trade_history.pop()

        return update_success

