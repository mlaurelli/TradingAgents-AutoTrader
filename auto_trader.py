"""
Auto Trader — TradingAgents + Alpaca Paper Trading (Autonomous Mode)

Esegue analisi e trading automatico su una lista di ticker a intervalli regolari.
Si ferma automaticamente quando i mercati USA chiudono.

Uso:
    python auto_trader.py
"""

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from telegram_notifier import TelegramNotifier

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Ticker da analizzare (high volatility + high volume)
TICKERS = ["NVDA", "TSLA", "AMD", "PLTR", "COIN"]  # Azioni ad alto movimento

# Quantità per singola operazione (ottimizzata per paper trading)
QTY_PER_TRADE = 3  # Aumentato per maggiore esposizione

# Intervallo tra un ciclo e l'altro (più frequente per cogliere opportunità)
# 900s = 15 min tra un ciclo completo e l'altro (massima reattività)
CYCLE_INTERVAL_SECONDS = 900

# Timezone mercati USA
ET = ZoneInfo("America/New_York")
IT = ZoneInfo("Europe/Rome")

# Orari mercato USA (Eastern Time)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path("auto_trader_logs")
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"auto_trader_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decision map (ottimizzata per aggressività)
# ---------------------------------------------------------------------------
DECISION_MAP = {
    "BUY": {"side": OrderSide.BUY, "weight": 1.0},        # Massima aggressività
    "OVERWEIGHT": {"side": OrderSide.BUY, "weight": 0.75}, # Più esposizione
    "HOLD": None,                                         # Nessuna azione
    "UNDERWEIGHT": {"side": OrderSide.SELL, "weight": 0.75}, # Più vendita
    "SELL": {"side": OrderSide.SELL, "weight": 1.0},      # Massima vendita
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_market_open() -> bool:
    """Controlla se il mercato USA è aperto (lun-ven, 9:30-16:00 ET)."""
    now_et = datetime.now(ET)
    # Weekend check
    if now_et.weekday() >= 5:  # 5=sabato, 6=domenica
        return False
    market_open = now_et.replace(
        hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0
    )
    market_close = now_et.replace(
        hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0
    )
    return market_open <= now_et <= market_close


def time_until_market_close() -> timedelta:
    """Ritorna il tempo rimanente fino alla chiusura del mercato."""
    now_et = datetime.now(ET)
    market_close = now_et.replace(
        hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0
    )
    return market_close - now_et


def get_alpaca_client() -> TradingClient:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY e ALPACA_SECRET_KEY mancanti nel .env")
        sys.exit(1)
    return TradingClient(api_key, secret_key, paper=True)


def show_account(client: TradingClient):
    account = client.get_account()
    logger.info("=" * 60)
    logger.info("CONTO ALPACA (Paper Trading)")
    logger.info(f"  Cash:        ${account.cash}")
    logger.info(f"  Portfolio:   ${account.portfolio_value}")
    logger.info(f"  Buying power: ${account.buying_power}")
    logger.info(f"  Equity:      ${account.equity}")
    logger.info("=" * 60)
    return account


def show_positions(client: TradingClient):
    positions = client.get_all_positions()
    if not positions:
        logger.info("Nessuna posizione aperta.")
        return
    logger.info("-" * 60)
    logger.info("POSIZIONI APERTE:")
    for p in positions:
        pnl = float(p.unrealized_pl)
        pnl_pct = float(p.unrealized_plpc) * 100
        emoji = "+" if pnl >= 0 else ""
        logger.info(
            f"  {p.symbol}: {p.qty} azioni @ ${p.avg_entry_price} "
            f"| P&L: {emoji}${pnl:.2f} ({emoji}{pnl_pct:.1f}%)"
        )
    logger.info("-" * 60)


def show_todays_orders(client: TradingClient):
    request = GetOrdersRequest(status=QueryOrderStatus.ALL)
    orders = client.get_orders(request)
    today = datetime.now(ET).date()
    todays = [o for o in orders if o.created_at.date() == today]
    if not todays:
        logger.info("Nessun ordine oggi.")
        return
    logger.info("-" * 60)
    logger.info(f"ORDINI DI OGGI ({len(todays)}):")
    for o in todays:
        logger.info(
            f"  [{o.status}] {o.side} {o.qty}x {o.symbol} @ {o.filled_avg_price or 'pending'}"
        )
    logger.info("-" * 60)


def get_current_position(client: TradingClient, ticker: str):
    """Ritorna la posizione corrente per un ticker, o None."""
    try:
        return client.get_open_position(ticker)
    except Exception:
        return None


def execute_decision(client: TradingClient, ticker: str, decision: str, qty: int, notifier: TelegramNotifier):
    """Esegue la decisione di trading con sizing dinamico."""
    decision_upper = decision.strip().upper()
    action = DECISION_MAP.get(decision_upper)

    if action is None:
        logger.info(f"  [{ticker}] HOLD → nessun ordine")
        return None

    # Sizing dinamico basato sulla forza della decisione
    base_qty = max(1, int(qty * action["weight"]))
    
    # Aumenta qty per decisioni forti (BUY/SELL) vs moderate (OVERWEIGHT/UNDERWEIGHT)
    if decision_upper in ["BUY", "SELL"]:
        effective_qty = base_qty * 2  # Massima esposizione per decisioni forti
        logger.info(f"  [{ticker}] Decisione FORTE ({decision_upper}) → qty raddoppiata: {effective_qty}")
    else:
        effective_qty = base_qty  # Qty standard per decisioni moderate
        logger.info(f"  [{ticker}] Decisione MODERATA ({decision_upper}) → qty standard: {effective_qty}")
    
    side = action["side"]

    # Safety check: se vuole vendere, verifica di avere la posizione
    if side == OrderSide.SELL:
        position = get_current_position(client, ticker)
        if position is None:
            logger.info(
                f"  [{ticker}] {decision_upper} → vuole vendere ma non abbiamo posizioni, skip"
            )
            return None
        available = int(float(position.qty))
        if effective_qty > available:
            effective_qty = available
            logger.info(
                f"  [{ticker}] Riduco qty vendita a {effective_qty} (posizione attuale)"
            )

    order_request = MarketOrderRequest(
        symbol=ticker,
        qty=effective_qty,
        side=side,
        time_in_force=TimeInForce.DAY,
    )

    logger.info(f"  [{ticker}] Invio ordine: {side.name} {effective_qty}x {ticker}")
    try:
        order = client.submit_order(order_request)
        logger.info(f"  [{ticker}] Ordine OK! ID: {order.id}, status: {order.status}")
        
        # Notifica Telegram
        notifier.send_trade(ticker, side.name, effective_qty, str(order.id))
        
        return order
    except Exception as e:
        logger.error(f"  [{ticker}] Errore ordine: {e}")
        notifier.send_error(ticker, str(e))
        return None


def log_decision(ticker: str, trade_date: str, decision: str, full_report: str, order):
    """Salva log JSON della decisione."""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "ticker": ticker,
        "trade_date": trade_date,
        "decision": decision,
        "full_report": full_report[:2000],  # troncato per non esplodere
        "order_id": str(order.id) if order else None,
        "order_status": str(order.status) if order else "NO_ORDER",
    }
    log_file = LOG_DIR / "decisions.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_cycle(client: TradingClient, config: dict, cycle_num: int, notifier: TelegramNotifier):
    """Esegue un ciclo completo: analizza tutti i ticker e opera."""
    trade_date = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"\n{'='*60}")
    logger.info(f"CICLO #{cycle_num} — {datetime.now(IT).strftime('%H:%M:%S')} ora italiana")
    logger.info(f"Data trading: {trade_date}")
    logger.info(f"Tempo alla chiusura: {time_until_market_close()}")
    logger.info(f"{'='*60}")

    for ticker in TICKERS:
        logger.info(f"\n>>> Analisi {ticker}...")
        try:
            ta = TradingAgentsGraph(debug=False, config=config)
            final_state, decision = ta.propagate(ticker, trade_date)

            full_report = final_state.get("final_trade_decision", "N/A")
            logger.info(f"  [{ticker}] Decisione agente: {decision}")

            # Notifica Telegram con report completo
            notifier.send_summary(ticker, decision, full_report)

            # Esegui l'ordine
            order = execute_decision(client, ticker, decision, QTY_PER_TRADE, notifier)

            # Log
            log_decision(ticker, trade_date, decision, full_report, order)

        except Exception as e:
            logger.error(f"  [{ticker}] ERRORE durante analisi: {e}")
            logger.error(traceback.format_exc())
            notifier.send_error(ticker, str(e))
            continue

        # Piccola pausa tra un ticker e l'altro
        time.sleep(5)

    # Riepilogo dopo il ciclo
    logger.info(f"\n--- Fine ciclo #{cycle_num} ---")
    show_positions(client)
    show_account(client)
    show_todays_orders(client)
    
    # Riepilogo su Telegram (ogni 15 minuti ≈ ogni ciclo)
    if cycle_num % 1 == 0:  # ogni ciclo ≈ ogni 30 min
        try:
            positions = client.get_all_positions()
            account = client.get_account()
            positions_data = [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_entry_price": p.avg_entry_price,
                    "unrealized_pl": p.unrealized_pl,
                }
                for p in positions
            ]
            account_data = {
                "portfolio_value": account.portfolio_value,
                "cash": account.cash,
                "buying_power": account.buying_power,
            }
            notifier.send_daily_summary(positions_data, account_data)
        except Exception as e:
            logger.error(f"Errore riepilogo Telegram: {e}")


def main():
    logger.info("=" * 60)
    logger.info("AUTO TRADER — TradingAgents + Alpaca Paper Trading + Telegram")
    logger.info(f"Ora locale: {datetime.now(IT).strftime('%Y-%m-%d %H:%M:%S')} (Italia)")
    logger.info(f"Ora ET:     {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')} (New York)")
    logger.info(f"Ticker:     {', '.join(TICKERS)}")
    logger.info(f"Qty/trade:  {QTY_PER_TRADE}")
    logger.info(f"Intervallo: {CYCLE_INTERVAL_SECONDS // 60} minuti tra i cicli")
    logger.info("=" * 60)

    # Config TradingAgents (ottimizzata con GPT-5.4-pro)
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "openai"
    config["deep_think_llm"] = "gpt-5.4"  # Veloce come 4o, più intelligente
    config["quick_think_llm"] = "gpt-5.4"  # Stesso modello per consistenza
    config["max_debate_rounds"] = 1  # Snello per velocità
    config["max_risk_discuss_rounds"] = 1  # Snello per velocità

    # Connetti Alpaca
    client = get_alpaca_client()
    show_account(client)

    # Inizializza Telegram Notifier
    notifier = TelegramNotifier()
    if notifier.enabled:
        logger.info("📱 Telegram notifiche attive")
    else:
        logger.info("📱 Telegram notifiche disabilitate")

    # Verifica mercato
    if not is_market_open():
        now_et = datetime.now(ET)
        logger.warning(
            f"Il mercato USA è attualmente CHIUSO "
            f"(ora ET: {now_et.strftime('%H:%M')}, giorno: {now_et.strftime('%A')}). "
            f"Orario: lun-ven 9:30-16:00 ET (15:30-22:00 Italia)."
        )
        logger.warning("Lancio comunque il primo ciclo come test...")

    cycle_num = 0

    while True:
        cycle_num += 1

        # Controlla se il mercato è aperto
        if is_market_open():
            run_cycle(client, config, cycle_num, notifier)
        else:
            now_et = datetime.now(ET)
            logger.info(
                f"Mercato chiuso (ET: {now_et.strftime('%A %H:%M')}). "
                f"In attesa..."
            )
            # Se è weekend o dopo le 16, aspetta più a lungo
            if now_et.weekday() >= 5:
                logger.info("È weekend. Il bot si ferma.")
                logger.info("Riepilogo finale:")
                show_positions(client)
                show_account(client)
                break

            # Se siamo dopo la chiusura di venerdì
            if now_et.weekday() == 4 and now_et.hour >= MARKET_CLOSE_HOUR:
                logger.info("Venerdì dopo la chiusura. Il bot si ferma.")
                logger.info("Riepilogo finale:")
                show_positions(client)
                show_account(client)
                break

        # Aspetta prima del prossimo ciclo
        remaining = time_until_market_close()
        if remaining.total_seconds() <= 0:
            logger.info("Mercato chiuso per oggi.")
            if datetime.now(ET).weekday() == 4:
                logger.info("Fine settimana raggiunta. Stop.")
                show_positions(client)
                show_account(client)
                break

        wait_time = min(CYCLE_INTERVAL_SECONDS, max(0, int(remaining.total_seconds())))
        if wait_time > 0:
            logger.info(
                f"Prossimo ciclo tra {wait_time // 60} minuti "
                f"({datetime.now(IT).strftime('%H:%M')} → "
                f"{(datetime.now(IT) + timedelta(seconds=wait_time)).strftime('%H:%M')} Italia)"
            )
            time.sleep(wait_time)
        else:
            break

    logger.info("\n" + "=" * 60)
    logger.info("AUTO TRADER TERMINATO")
    logger.info("Controlla i log in: auto_trader_logs/")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
