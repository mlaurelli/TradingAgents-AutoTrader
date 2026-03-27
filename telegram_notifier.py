"""
Telegram Notifier per Auto Trader

Invia notifiche quando il bot esegue operazioni.

Setup:
1. Crea un bot su Telegram: @BotFather → /newbot → ottieni token
2. Ottieni il tuo chat ID: manda messaggio al bot → @userinfobot
3. Aggiungi token e chat_id al .env

Uso:
    from telegram_notifier import TelegramNotifier
    notifier = TelegramNotifier()
    notifier.send_trade("NVDA", "BUY", 2, order_id="12345")
"""

import os
import logging
from typing import Optional

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Invia notifiche Telegram per le operazioni di trading."""

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id and requests)

        if not self.enabled:
            logger.warning(
                "Telegram notifiche disabilitate. "
                "Imposta TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nel .env"
            )

    def _send_message(self, message: str) -> bool:
        """Invia un messaggio al bot Telegram."""
        if not self.enabled:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}

            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()

            logger.info("Notifica Telegram inviata")
            return True

        except Exception as e:
            logger.error(f"Errore notifica Telegram: {e}")
            return False

    def send_trade(self, ticker: str, action: str, qty: int, order_id: Optional[str] = None):
        """Notifica un'operazione di trading."""
        if not self.enabled:
            return

        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action.upper(), "❓")

        message = f"""
{emoji} **TRADING AGENTS - OPERAZIONE**

📈 **Ticker**: `{ticker}`
💰 **Azione**: `{action}`
📊 **Quantità**: `{qty}`
🆔 **Order ID**: `{order_id or 'N/A'}`
⏰ **Ora**: `{_now_italy()}`

_🤖 Powered by TradingAgents + Alpaca_
        """.strip()

        self._send_message(message)

    def send_summary(self, ticker: str, decision: str, full_report: str):
        """Notifica il report completo del Portfolio Manager."""
        if not self.enabled:
            return

        # Tronca il report per non superare i limiti Telegram
        report_preview = full_report[:500] + "..." if len(full_report) > 500 else full_report

        message = f"""
📊 **TRADING AGENTS - ANALISI COMPLETA**

📈 **Ticker**: `{ticker}`
🎯 **Decisione**: `{decision}`

**Report Portfolio Manager**:
```
{report_preview}
```

⏰ **Ora**: `{_now_italy()}`

_🤖 Powered by TradingAgents_
        """.strip()

        self._send_message(message)

    def send_error(self, ticker: str, error: str):
        """Notifica un errore."""
        if not self.enabled:
            return

        message = f"""
⚠️ **TRADING AGENTS - ERRORE**

📈 **Ticker**: `{ticker}`
❌ **Errore**: `{error}`

⏰ **Ora**: `{_now_italy()}`
        """.strip()

        self._send_message(message)

    def send_daily_summary(self, positions: list, account: dict):
        """Invia riepilogo con P&L dettagliato."""
        if not self.enabled:
            return

        # Calcola P&L totale e performance
        total_pnl = sum(float(p.get("unrealized_pl", 0)) for p in positions)
        portfolio_value = float(account.get('portfolio_value', 100000))
        initial_capital = 100000.0
        performance_pct = ((portfolio_value - initial_capital) / initial_capital) * 100
        
        # Emoji per performance
        if performance_pct > 0:
            perf_emoji = "🚀"
            perf_color = "🟢"
        elif performance_pct < 0:
            perf_emoji = "📉"
            perf_color = "🔴"
        else:
            perf_emoji = "➡️"
            perf_color = "⚪"

        pos_text = ""
        for p in positions:
            pnl = float(p.get("unrealized_pl", 0))
            emoji = "🟢" if pnl >= 0 else "🔴"
            pos_text += f"{emoji} `{p['symbol']}`: {p['qty']} @ ${p['avg_entry_price']} (P&L: ${pnl:.2f})\n"

        message = f"""
{perf_emoji} **TRADING AGENTS - PERFORMANCE**

💰 **Portfolio**: `${portfolio_value:,.2f}`
💵 **Cash**: `${account.get('cash', 'N/A')}`
⚡ **Buying Power**: `${account.get('buying_power', 'N/A')}`
📊 **P&L Totale**: `{perf_color} ${total_pnl:+.2f}`
📈 **Performance**: `{perf_color} {performance_pct:+.2f}%`

**Posizioni Aperte** ({len(positions)}):
{pos_text or "Nessuna posizione"}

⏰ **Ora**: `{_now_italy()}`
_🤖 TradingAgents Auto Trader_
        """.strip()

        self._send_message(message)

        # Controlla target speciali
        self._check_milestones(performance_pct, total_pnl)

    def _check_milestones(self, performance_pct: float, total_pnl: float):
        """Controlla e notifica target importanti."""
        milestones = [
            (5.0, "🎯 **TARGET +5% RAGGIUNTO!**"),
            (10.0, "🏆 **TARGET +10% RAGGIUNTO!**"),
            (-5.0, "⚠️ **LOSS -5% ATTENZIONE!**"),
            (-10.0, "🚨 **LOSS -10% CRITICO!**"),
        ]
        
        for threshold, message in milestones:
            if abs(performance_pct - threshold) < 0.1:  # entro 0.1% del target
                milestone_msg = f"""
{message}

📊 **Performance**: {performance_pct:+.2f}%
💰 **P&L Totale**: ${total_pnl:+.2f}

⏰ **Ora**: `{_now_italy()}`
_🤖 TradingAgents Auto Trader_
                """.strip()
                self._send_message(milestone_msg)


def _now_italy() -> str:
    """Ora corrente in formato Italia."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M:%S")


# Test rapido
if __name__ == "__main__":
    notifier = TelegramNotifier()
    if notifier.enabled:
        notifier.send_trade("NVDA", "BUY", 2, "test-123")
        print("Test notifica inviata")
    else:
        print("Telegram non configurato")
