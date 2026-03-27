"""
Bridge TradingAgents → Alpaca Paper Trading

Questo script:
1. Esegue TradingAgents per un ticker e una data
2. Interpreta la decisione (BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL)
3. Esegue l'ordine su Alpaca Paper Trading

Uso:
    python bridge_alpaca.py --ticker NVDA --date 2026-03-27
    python bridge_alpaca.py --ticker NVDA --date 2026-03-27 --dry-run
    python bridge_alpaca.py --ticker NVDA --date 2026-03-27 --qty 5
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path("bridge_logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "bridge.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping decisione → azione
# ---------------------------------------------------------------------------
# Mappa i 5 rating del Portfolio Manager ad azioni concrete.
# - BUY / OVERWEIGHT → acquista
# - SELL / UNDERWEIGHT → vendi
# - HOLD → nessuna azione
DECISION_MAP = {
    "BUY": {"side": OrderSide.BUY, "weight": 1.0},
    "OVERWEIGHT": {"side": OrderSide.BUY, "weight": 0.5},
    "HOLD": None,  # nessun ordine
    "UNDERWEIGHT": {"side": OrderSide.SELL, "weight": 0.5},
    "SELL": {"side": OrderSide.SELL, "weight": 1.0},
}


def get_alpaca_client() -> TradingClient:
    """Crea il client Alpaca dal .env."""
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        logger.error(
            "ALPACA_API_KEY e ALPACA_SECRET_KEY devono essere impostati nel file .env"
        )
        sys.exit(1)

    # paper=True → conto di paper trading (soldi virtuali)
    return TradingClient(api_key, secret_key, paper=True)


def run_trading_agents(ticker: str, trade_date: str, config: dict):
    """Esegue TradingAgents e restituisce (full_state, decision_str)."""
    logger.info(f"Avvio TradingAgents per {ticker} al {trade_date}...")
    ta = TradingAgentsGraph(debug=True, config=config)
    final_state, decision = ta.propagate(ticker, trade_date)
    logger.info(f"Decisione TradingAgents: {decision}")
    return final_state, decision


def execute_order(
    client: TradingClient,
    ticker: str,
    decision: str,
    qty: int,
    dry_run: bool = False,
):
    """Traduce la decisione in un ordine Alpaca e lo esegue."""

    decision_upper = decision.strip().upper()
    action = DECISION_MAP.get(decision_upper)

    if action is None:
        logger.info(f"Decisione = {decision_upper} → HOLD, nessun ordine inviato.")
        return None

    # Calcola quantità in base al peso della decisione
    effective_qty = max(1, int(qty * action["weight"]))

    order_request = MarketOrderRequest(
        symbol=ticker,
        qty=effective_qty,
        side=action["side"],
        time_in_force=TimeInForce.DAY,
    )

    if dry_run:
        logger.info(
            f"[DRY RUN] Ordine: {action['side'].name} {effective_qty}x {ticker} "
            f"(decisione: {decision_upper}, peso: {action['weight']})"
        )
        return {"dry_run": True, "side": action["side"].name, "qty": effective_qty}

    logger.info(
        f"Invio ordine: {action['side'].name} {effective_qty}x {ticker}..."
    )
    order = client.submit_order(order_request)
    logger.info(f"Ordine eseguito! ID: {order.id}, status: {order.status}")
    return order


def log_decision(ticker: str, trade_date: str, decision: str, order_result):
    """Salva un log JSON della decisione e dell'ordine."""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "ticker": ticker,
        "trade_date": trade_date,
        "agent_decision": decision,
        "order": str(order_result) if order_result else "HOLD - no order",
    }
    log_file = LOG_DIR / f"decisions_{ticker}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    logger.info(f"Log salvato in {log_file}")


def show_account_summary(client: TradingClient):
    """Mostra un riepilogo del conto."""
    account = client.get_account()
    logger.info("=" * 50)
    logger.info("RIEPILOGO CONTO ALPACA (Paper Trading)")
    logger.info(f"  Cash disponibile:  ${account.cash}")
    logger.info(f"  Valore portfolio:  ${account.portfolio_value}")
    logger.info(f"  Buying power:      ${account.buying_power}")
    logger.info(f"  Equity:            ${account.equity}")
    logger.info("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="Bridge TradingAgents → Alpaca Paper Trading"
    )
    parser.add_argument(
        "--ticker", required=True, help="Ticker da analizzare (es. NVDA, AAPL, TSLA)"
    )
    parser.add_argument(
        "--date", required=True, help="Data di trading (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--qty", type=int, default=10, help="Quantità base di azioni (default: 10)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula senza inviare ordini reali ad Alpaca",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        choices=["openai", "google", "anthropic", "xai", "openrouter", "ollama"],
        help="Provider LLM (default: openai)",
    )
    parser.add_argument(
        "--deep-model", default=None, help="Modello per ragionamento complesso"
    )
    parser.add_argument(
        "--quick-model", default=None, help="Modello per task rapidi"
    )

    args = parser.parse_args()

    # Configura TradingAgents
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = args.provider
    if args.deep_model:
        config["deep_think_llm"] = args.deep_model
    if args.quick_model:
        config["quick_think_llm"] = args.quick_model

    # Connetti ad Alpaca
    if not args.dry_run:
        client = get_alpaca_client()
        show_account_summary(client)
    else:
        client = None
        logger.info("[DRY RUN] Modalità simulazione attiva, nessun ordine verrà inviato.")

    # Esegui analisi TradingAgents
    final_state, decision = run_trading_agents(args.ticker, args.date, config)

    # Stampa il report completo del Portfolio Manager
    logger.info("\n" + "=" * 50)
    logger.info("REPORT COMPLETO DEL PORTFOLIO MANAGER:")
    logger.info("=" * 50)
    logger.info(final_state.get("final_trade_decision", "N/A"))
    logger.info("=" * 50)
    logger.info(f"DECISIONE ESTRATTA: {decision}")
    logger.info("=" * 50 + "\n")

    # Esegui ordine
    order_result = execute_order(client, args.ticker, decision, args.qty, args.dry_run)

    # Log
    log_decision(args.ticker, args.date, decision, order_result)

    # Riepilogo finale
    if client and not args.dry_run:
        show_account_summary(client)

    logger.info("Done!")


if __name__ == "__main__":
    main()
