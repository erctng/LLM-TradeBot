import requests
import json
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

VPS_IP = "49.13.235.188"
PORT = 8085
API_TOKEN = "super-secret-token-123"

def fetch_metrics():
    url = f"http://{VPS_IP}:{PORT}/metrics"
    headers = {"x-token": API_TOKEN}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[bold red]Failed to connect to VPS Telemetry API: {e}[/bold red]")
        return None

def display_dashboard(data: dict):
    console.clear()
    console.print(Panel(f"[bold cyan]LLM-TradeBot Live Telemetry[/bold cyan] (Connected to {VPS_IP})"))
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="dim", width=20)
    table.add_column("Value", justify="right")
    
    table.add_row("Total Trades (DB)", str(data.get("total_trades", 0)))
    table.add_row("Closed Trades", str(data.get("closed_trades", 0)))
    table.add_row("Win Rate", f"{data.get('win_rate', 0):.2f}%")
    table.add_row("Sharpe Ratio", f"{data.get('sharpe_ratio', 0):.2f}")
    
    pnl = data.get("total_pnl", 0)
    pnl_color = "green" if pnl >= 0 else "red"
    table.add_row("Total PnL", f"[{pnl_color}]{pnl:.2f}[/{pnl_color}]")
    
    table.add_row("Avg Win / Avg Loss", f"{data.get('average_win', 0):.2f} / {data.get('average_loss', 0):.2f}")
    table.add_row("Reward/Risk Ratio", f"{data.get('reward_risk_ratio', 0):.2f}")
    
    dd = data.get("max_drawdown_pct", 0)
    dd_color = "red" if dd > 10 else "yellow"
    table.add_row("Max Drawdown", f"[{dd_color}]{dd:.2f}[/{dd_color}]")
    
    console.print(table)
    
    losses = data.get("recent_losses", [])
    if losses:
        console.print("\n[bold red]Recent Losses:[/bold red]")
        for l in losses:
            console.print(f"- {l['timestamp']}: PnL {l['pnl']:.2f}, Reason: {l['reason']}")

if __name__ == "__main__":
    console.print("[yellow]Fetching data from VPS...[/yellow]")
    data = fetch_metrics()
    if data:
        display_dashboard(data)
