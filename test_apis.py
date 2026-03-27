"""Test rapido delle API necessarie al bot."""
from dotenv import load_dotenv
load_dotenv()
import os

print("=== TEST ALPACA API ===", flush=True)
try:
    from alpaca.trading.client import TradingClient
    tc = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)
    account = tc.get_account()
    print(f"  Alpaca OK", flush=True)
    print(f"  Cash: ${account.cash}", flush=True)
    print(f"  Portfolio: ${account.portfolio_value}", flush=True)
    print(f"  Status: {account.status}", flush=True)
    print(f"  Trading bloccato: {account.trading_blocked}", flush=True)
except Exception as e:
    print(f"  Alpaca ERRORE: {e}", flush=True)

print(flush=True)
print("=== TEST MODELLI GPT (usati dal bot) ===", flush=True)
from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
for model in ["gpt-5.2", "gpt-5-mini"]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5,
        )
        print(f"  {model}: OK - {r.choices[0].message.content}", flush=True)
    except Exception as e:
        print(f"  {model}: ERRORE - {e}", flush=True)

print(flush=True)
print("=== TEST YFINANCE (dati di mercato) ===", flush=True)
try:
    import yfinance as yf
    ticker = yf.Ticker("NVDA")
    info = ticker.info
    print(f"  yfinance OK - NVDA prezzo: ${info.get('currentPrice', 'N/A')}", flush=True)
except Exception as e:
    print(f"  yfinance ERRORE: {e}", flush=True)

print(flush=True)
print("=== TUTTI I TEST COMPLETATI ===", flush=True)
