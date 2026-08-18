"""Circuit breaker wiring tests.

These lock down the fix for the defect where RiskManager.record_trade() and
update_drawdown() had no production caller, leaving consecutive_losses and
total_drawdown_pct pinned at zero so neither breaker could ever fire.
"""
import pytest

from src.risk.manager import RiskManager


def _closed(pnl: float) -> dict:
    return {'action': 'CLOSE_LONG', 'status': 'CLOSED', 'pnl': pnl}


def _opened() -> dict:
    return {'action': 'OPEN_LONG', 'status': 'OPEN', 'pnl': 0.0}


def test_consecutive_losses_accumulate_on_closed_trades():
    rm = RiskManager()
    rm.max_consecutive_losses = 3

    for _ in range(3):
        rm.record_trade(_closed(-10.0))

    assert rm.consecutive_losses == 3
    allowed, reason = rm.check_circuit_breakers()
    assert allowed is False
    assert "连续亏损" in reason


def test_open_records_do_not_reset_loss_counter():
    """An OPEN record carries pnl=0; counting it would clear the streak."""
    rm = RiskManager()
    rm.record_trade(_closed(-10.0))
    rm.record_trade(_closed(-10.0))
    assert rm.consecutive_losses == 2

    rm.record_trade(_opened())
    assert rm.consecutive_losses == 2, "opening a position must not clear the loss streak"


def test_win_resets_loss_counter():
    rm = RiskManager()
    rm.record_trade(_closed(-10.0))
    rm.record_trade(_closed(-10.0))
    rm.record_trade(_closed(+5.0))
    assert rm.consecutive_losses == 0
    assert rm.check_circuit_breakers()[0] is True


def test_drawdown_breaker_trips_and_is_never_negative():
    rm = RiskManager()
    rm.stop_trading_drawdown_pct = 10.0

    rm.update_drawdown(current_balance=950.0, peak_balance=1000.0)
    assert rm.total_drawdown_pct == pytest.approx(5.0)
    assert rm.check_circuit_breakers()[0] is True

    rm.update_drawdown(current_balance=880.0, peak_balance=1000.0)
    assert rm.total_drawdown_pct == pytest.approx(12.0)
    allowed, reason = rm.check_circuit_breakers()
    assert allowed is False
    assert "回撤" in reason

    # Equity above the peak must not produce a negative drawdown
    rm.update_drawdown(current_balance=1100.0, peak_balance=1000.0)
    assert rm.total_drawdown_pct == 0.0


def test_state_observer_feeds_risk_manager():
    """The wiring itself: recording a trade on global_state must move risk state."""
    from src.server.state import global_state

    rm = RiskManager()
    rm.max_consecutive_losses = 2
    global_state.register_trade_observer(
        lambda trade, equity, peak: rm.record_trade(trade)
    )
    global_state.set_risk_gate(rm.check_circuit_breakers)

    global_state.record_trade(_closed(-25.0))
    global_state.record_trade(_closed(-25.0))

    assert rm.consecutive_losses == 2
    allowed, _ = global_state.check_risk_gate()
    assert allowed is False, "global_state gate must reflect accumulated risk state"


def test_risk_gate_fails_open_when_unset():
    """No gate registered (backtest harness) must never block trading."""
    from src.server.state import SharedState

    fresh = SharedState()
    assert fresh.check_risk_gate() == (True, "")
