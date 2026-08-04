import os
import sqlite3
import pandas as pd
import numpy as np
from fastapi import FastAPI, Header, HTTPException, Depends
from typing import Optional
from pydantic import BaseModel

app = FastAPI(title="LLM-TradeBot VPS Telemetry")

# Hardcoded or Env token for simplicity
API_TOKEN = os.getenv("VPS_API_TOKEN", "super-secret-token-123")
DB_PATH = os.getenv("DB_PATH", "/root/LLM-TradeBot/data/analytics/trading.db")

def verify_token(x_token: str = Header(...)):
    if x_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized Token")
    return x_token

class PerformanceMetrics(BaseModel):
    total_trades: int
    closed_trades: int
    win_rate: float
    total_pnl: float
    average_win: float
    average_loss: float
    reward_risk_ratio: float
    max_drawdown_pct: float
    sharpe_ratio: float
    recent_losses: list

def calculate_drawdown(pnl_series: pd.Series) -> float:
    cumulative = pnl_series.cumsum()
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = (running_max - cumulative)
    max_dd = drawdowns.max() if not drawdowns.empty else 0
    return float(max_dd)

def calculate_sharpe(pnl_series: pd.Series, risk_free_rate: float = 0.0) -> float:
    if len(pnl_series) < 2:
        return 0.0
    avg = pnl_series.mean()
    std = pnl_series.std()
    if std == 0:
        return 0.0
    # Annualized multiplier assuming these are daily or frequent trades, 
    # but for simplicity we return per-trade sharpe
    return float((avg - risk_free_rate) / std)

@app.get("/metrics", response_model=PerformanceMetrics, dependencies=[Depends(verify_token)])
async def get_metrics():
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="Database not found")
        
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT * FROM trades", conn)
        conn.close()
        
        if df.empty:
            return PerformanceMetrics(
                total_trades=0, closed_trades=0, win_rate=0.0, total_pnl=0.0,
                average_win=0.0, average_loss=0.0, reward_risk_ratio=0.0,
                max_drawdown_pct=0.0, sharpe_ratio=0.0, recent_losses=[]
            )
            
        # Extract closed trades
        closed = df[df['action'] == 'close'].copy()
        if closed.empty:
            # Fallback if action strings differ
            closed = df[df['pnl'].notna() & (df['pnl'] != 0)].copy()
            
        if closed.empty:
            return PerformanceMetrics(
                total_trades=len(df), closed_trades=0, win_rate=0.0, total_pnl=0.0,
                average_win=0.0, average_loss=0.0, reward_risk_ratio=0.0,
                max_drawdown_pct=0.0, sharpe_ratio=0.0, recent_losses=[]
            )
            
        closed['pnl'] = pd.to_numeric(closed['pnl'], errors='coerce').fillna(0)
        wins = closed[closed['pnl'] > 0]
        losses = closed[closed['pnl'] <= 0]
        
        win_rate = len(wins) / len(closed) * 100
        avg_win = wins['pnl'].mean() if not wins.empty else 0
        avg_loss = losses['pnl'].mean() if not losses.empty else 0
        rr_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        total_pnl = closed['pnl'].sum()
        
        max_dd = calculate_drawdown(closed['pnl'])
        sharpe = calculate_sharpe(closed['pnl'])
        
        recent_losses_list = []
        for idx, row in losses.tail(5).iterrows():
            recent_losses_list.append({
                "timestamp": str(row.get('timestamp', '')),
                "pnl": float(row['pnl']),
                "reason": str(row.get('reason', 'N/A'))
            })
            
        return PerformanceMetrics(
            total_trades=len(df),
            closed_trades=len(closed),
            win_rate=float(win_rate),
            total_pnl=float(total_pnl),
            average_win=float(avg_win),
            average_loss=float(avg_loss),
            reward_risk_ratio=float(rr_ratio),
            max_drawdown_pct=float(max_dd),
            sharpe_ratio=float(sharpe),
            recent_losses=recent_losses_list
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Allow 0.0.0.0 so the VPS can accept external requests
    uvicorn.run(app, host="0.0.0.0", port=8085)
