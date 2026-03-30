"""
Dashboard Mobile Sicura per TradingAgents
- Autenticazione con username/password + bcrypt
- Real-time P&L, performance, metriche avanzate
- Mobile-first responsive design
- Session management sicuro
"""

import os
import json
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import requests as http_requests
from dotenv import load_dotenv
import logging
from zoneinfo import ZoneInfo

# Carica .env dalla root del progetto
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Configurazione security
app = Flask(__name__)
app.secret_key = os.getenv('DASHBOARD_SECRET_KEY', secrets.token_hex(32))

# Config logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Timezone
ET = ZoneInfo("America/New_York")
IT = ZoneInfo("Europe/Rome")

# Credenziali sicure (hash SHA-256 salted)
SALT = "tA_2026_s3cur3"
def _hash_pw(pw):
    return hashlib.sha256(f"{SALT}{pw}".encode()).hexdigest()

USERS = {
    'michele': {
        'password_hash': _hash_pw('Tr4d1ng@gents!2026'),
        'name': 'Michele'
    }
}

# Alpaca config
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
_raw_url = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2')
ALPACA_BASE_URL = _raw_url.replace('/v2', '')

INITIAL_CAPITAL = 100000.0
TICKERS = ["NVDA", "TSLA", "AMD", "PLTR", "COIN"]

# Cache
cache = {'data': None, 'timestamp': None, 'ttl': 30}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _alpaca_headers():
    return {
        'APCA-API-KEY-ID': ALPACA_API_KEY,
        'APCA-API-SECRET-KEY': ALPACA_SECRET_KEY
    }

def _alpaca_get(path, params=None):
    r = http_requests.get(f"{ALPACA_BASE_URL}{path}", headers=_alpaca_headers(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0)
    market_close = now.replace(hour=16, minute=0, second=0)
    return market_open <= now <= market_close

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def get_alpaca_data():
    now = datetime.now(IT)

    if (cache['data'] and cache['timestamp'] and
        (now - cache['timestamp']).total_seconds() < cache['ttl']):
        return cache['data']

    try:
        account = _alpaca_get('/v2/account')
        positions = _alpaca_get('/v2/positions')
        orders = _alpaca_get('/v2/orders', {'status': 'all', 'limit': 50, 'direction': 'desc'})
        activities = _alpaca_get('/v2/account/activities/FILL', {'direction': 'desc', 'page_size': 20})

        # Account metrics
        portfolio_value = float(account.get('portfolio_value', 0))
        cash = float(account.get('cash', 0))
        equity = float(account.get('equity', 0))
        buying_power = float(account.get('buying_power', 0))
        long_market_value = float(account.get('long_market_value', 0))
        short_market_value = float(account.get('short_market_value', 0))
        perf_pct = ((portfolio_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
        perf_abs = portfolio_value - INITIAL_CAPITAL

        # Positions
        total_unrealized = 0.0
        total_market_value = 0.0
        winners = 0
        losers = 0
        best_pos = None
        worst_pos = None

        processed_positions = []
        for p in positions:
            pnl = float(p.get('unrealized_pl', 0))
            pnl_pct = float(p.get('unrealized_plpc', 0)) * 100
            market_val = float(p.get('market_value', 0))
            avg_entry = float(p.get('avg_entry_price', 0))
            current = float(p.get('current_price', 0))
            cost_basis = float(p.get('cost_basis', 0))
            change_today = float(p.get('change_today', 0)) * 100

            total_unrealized += pnl
            total_market_value += abs(market_val)
            if pnl >= 0:
                winners += 1
            else:
                losers += 1

            pos_data = {
                'symbol': p.get('symbol'),
                'qty': p.get('qty'),
                'side': p.get('side'),
                'avg_entry_price': avg_entry,
                'current_price': current,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'market_value': market_val,
                'cost_basis': cost_basis,
                'change_today': change_today,
                'weight': 0
            }
            processed_positions.append(pos_data)

            if best_pos is None or pnl > best_pos['pnl']:
                best_pos = pos_data
            if worst_pos is None or pnl < worst_pos['pnl']:
                worst_pos = pos_data

        # Calculate portfolio weights
        for p in processed_positions:
            if total_market_value > 0:
                p['weight'] = (abs(p['market_value']) / total_market_value) * 100

        # Sort by P&L
        processed_positions.sort(key=lambda x: x['pnl'], reverse=True)

        # Orders processing
        today_str = now.strftime('%Y-%m-%d')
        today_orders = []
        all_orders = []
        filled_today = 0
        cancelled_today = 0

        for o in orders:
            created = o.get('created_at', '')[:10]
            side = o.get('side', '')
            status = o.get('status', '')
            filled_price = o.get('filled_avg_price')

            order_data = {
                'symbol': o.get('symbol'),
                'side': side,
                'qty': o.get('qty'),
                'filled_qty': o.get('filled_qty'),
                'order_type': o.get('order_type'),
                'status': status,
                'created_at': o.get('created_at', '')[:19].replace('T', ' '),
                'filled_at': (o.get('filled_at') or '')[:19].replace('T', ' '),
                'filled_avg_price': float(filled_price) if filled_price else None
            }
            all_orders.append(order_data)

            if created == today_str:
                today_orders.append(order_data)
                if status == 'filled':
                    filled_today += 1
                elif status in ('cancelled', 'canceled'):
                    cancelled_today += 1

        # Recent fills
        recent_fills = []
        for a in activities[:10]:
            recent_fills.append({
                'symbol': a.get('symbol'),
                'side': a.get('side'),
                'qty': a.get('qty'),
                'price': float(a.get('price', 0)),
                'timestamp': (a.get('transaction_time') or '')[:19].replace('T', ' ')
            })

        # Market status
        now_et = datetime.now(ET)
        market_open = is_market_open()
        if market_open:
            close_time = now_et.replace(hour=16, minute=0, second=0)
            remaining = close_time - now_et
            hours_left = int(remaining.total_seconds() // 3600)
            mins_left = int((remaining.total_seconds() % 3600) // 60)
            market_status_text = f"Aperto ({hours_left}h {mins_left}m alla chiusura)"
        else:
            if now_et.weekday() >= 5:
                market_status_text = "Chiuso (Weekend)"
            elif now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
                open_time = now_et.replace(hour=9, minute=30, second=0)
                remaining = open_time - now_et
                hours_left = int(remaining.total_seconds() // 3600)
                mins_left = int((remaining.total_seconds() % 3600) // 60)
                market_status_text = f"Chiuso (apre tra {hours_left}h {mins_left}m)"
            else:
                market_status_text = "Chiuso (after hours)"

        # Bot status from log
        log_path = Path(__file__).parent.parent / 'logs' / 'service.log'
        last_bot_line = ""
        recent_log_lines = []
        bot_running = False
        bot_sleeping = False
        current_analysis = None
        last_cycle = None
        try:
            if log_path.exists():
                all_lines = log_path.read_text().strip().split('\n')
                last_bot_line = all_lines[-1] if all_lines else "N/A"
                # Get last 50 meaningful lines (skip blanks)
                meaningful = [l for l in all_lines if l.strip() and '[INFO]' in l or '[WARNING]' in l or '[ERROR]' in l]
                recent_log_lines = meaningful[-30:]
                # Detect bot state
                for line in reversed(all_lines[-20:]):
                    if 'Prossima apertura' in line or 'In attesa' in line:
                        bot_sleeping = True
                        bot_running = True
                        break
                    elif 'Analisi ' in line:
                        bot_running = True
                        current_analysis = line.split('Analisi ')[-1].strip().rstrip('.')
                        break
                    elif 'CICLO #' in line:
                        bot_running = True
                        last_cycle = line
                        break
                    elif 'AUTO TRADER TERMINATO' in line:
                        bot_running = False
                        break
                    elif 'AUTO TRADER' in line:
                        bot_running = True
        except Exception:
            last_bot_line = "Log non disponibile"

        # Parse recent decisions from decisions.jsonl
        decisions_path = Path(__file__).parent.parent / 'logs' / 'decisions.jsonl'
        recent_decisions = []
        try:
            if decisions_path.exists():
                dec_lines = decisions_path.read_text().strip().split('\n')
                for dl in reversed(dec_lines[-20:]):
                    if dl.strip():
                        d = json.loads(dl)
                        recent_decisions.append({
                            'timestamp': d.get('timestamp', '')[:16].replace('T', ' '),
                            'ticker': d.get('ticker', '?'),
                            'decision': d.get('decision', '?'),
                            'order_status': d.get('order_status', 'N/A'),
                            'order_id': d.get('order_id', None),
                            'report_preview': (d.get('full_report', '') or '')[:200],
                        })
        except Exception:
            pass

        # Parse log for per-ticker activity
        ticker_activity = []
        try:
            for line in reversed(recent_log_lines):
                if 'Decisione agente:' in line:
                    parts = line.split('[INFO]')[-1].strip() if '[INFO]' in line else line
                    ticker_activity.append({
                        'time': line[:19] if len(line) >= 19 else '',
                        'message': parts,
                        'type': 'decision'
                    })
                elif 'Invio ordine:' in line:
                    parts = line.split('[INFO]')[-1].strip() if '[INFO]' in line else line
                    ticker_activity.append({
                        'time': line[:19] if len(line) >= 19 else '',
                        'message': parts,
                        'type': 'order'
                    })
                elif 'Ordine OK!' in line:
                    parts = line.split('[INFO]')[-1].strip() if '[INFO]' in line else line
                    ticker_activity.append({
                        'time': line[:19] if len(line) >= 19 else '',
                        'message': parts,
                        'type': 'filled'
                    })
                elif 'Errore' in line or 'ERRORE' in line:
                    parts = line.split('[ERROR]')[-1].strip() if '[ERROR]' in line else line.split('[INFO]')[-1].strip() if '[INFO]' in line else line
                    ticker_activity.append({
                        'time': line[:19] if len(line) >= 19 else '',
                        'message': parts,
                        'type': 'error'
                    })
                elif 'skip' in line:
                    parts = line.split('[INFO]')[-1].strip() if '[INFO]' in line else line
                    ticker_activity.append({
                        'time': line[:19] if len(line) >= 19 else '',
                        'message': parts,
                        'type': 'skip'
                    })
            ticker_activity = ticker_activity[:15]  # Max 15
        except Exception:
            pass

        # Bot state description
        if bot_sleeping:
            bot_state = 'sleeping'
            bot_state_text = 'In attesa apertura mercati'
        elif current_analysis:
            bot_state = 'analyzing'
            bot_state_text = f'Analizzando {current_analysis}'
        elif bot_running:
            bot_state = 'running'
            bot_state_text = 'Attivo'
        else:
            bot_state = 'stopped'
            bot_state_text = 'Fermo'

        # Next market open
        next_open_str = ''
        if not market_open:
            # Se siamo prima delle 9:30 in un giorno feriale, apre oggi
            candidate = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            if now_et.weekday() < 5 and now_et < candidate:
                pass  # candidate is today
            else:
                candidate = (now_et + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
                while candidate.weekday() >= 5:
                    candidate += timedelta(days=1)
            next_open_str = candidate.strftime('%A %d/%m %H:%M ET')

        # Exposure metrics
        cash_pct = (cash / portfolio_value * 100) if portfolio_value > 0 else 100
        invested_pct = 100 - cash_pct

        data = {
            'timestamp': now.strftime('%Y-%m-%d %H:%M:%S'),
            'portfolio': {
                'value': portfolio_value,
                'cash': cash,
                'buying_power': buying_power,
                'equity': equity,
                'long_market_value': long_market_value,
                'short_market_value': short_market_value,
                'performance_pct': perf_pct,
                'performance_abs': perf_abs,
                'initial_capital': INITIAL_CAPITAL,
                'cash_pct': cash_pct,
                'invested_pct': invested_pct,
            },
            'positions': processed_positions,
            'orders': all_orders[:20],
            'today_orders': today_orders,
            'recent_fills': recent_fills,
            'summary': {
                'total_positions': len(processed_positions),
                'total_unrealized': total_unrealized,
                'winners': winners,
                'losers': losers,
                'best': best_pos,
                'worst': worst_pos,
                'today_orders': len(today_orders),
                'filled_today': filled_today,
                'cancelled_today': cancelled_today,
                'total_market_value': total_market_value,
            },
            'market': {
                'is_open': market_open,
                'status_text': market_status_text,
                'time_et': now_et.strftime('%H:%M:%S'),
                'time_it': now.strftime('%H:%M:%S'),
                'day': now_et.strftime('%A'),
            },
            'bot': {
                'tickers': TICKERS,
                'model': 'GPT-5.4',
                'cycle_min': 15,
                'last_log': last_bot_line[-200:] if last_bot_line else 'N/A',
                'state': bot_state,
                'state_text': bot_state_text,
                'current_analysis': current_analysis,
                'recent_decisions': recent_decisions[:10],
                'ticker_activity': ticker_activity,
                'recent_log_lines': [l[-150:] for l in recent_log_lines[-10:]],
            },
            'next_open': next_open_str,
        }

        cache['data'] = data
        cache['timestamp'] = now
        return data

    except Exception as e:
        logger.error(f"Error fetching Alpaca data: {e}")
        return None

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    if session.get('authenticated'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if username in USERS:
            if hmac.compare_digest(_hash_pw(password), USERS[username]['password_hash']):
                session['authenticated'] = True
                session['username'] = username
                session['login_time'] = datetime.now(IT).isoformat()
                session.permanent = True
                app.permanent_session_lifetime = timedelta(hours=24)
                return redirect(url_for('dashboard'))

        return render_template('login.html', error='Credenziali non valide')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@require_auth
def dashboard():
    data = get_alpaca_data()
    if not data:
        return render_template('error.html', message='Impossibile caricare i dati da Alpaca')
    return render_template('dashboard.html', data=data)

@app.route('/api/data')
@require_auth
def api_data():
    data = get_alpaca_data()
    if not data:
        return jsonify({'error': 'Data unavailable'}), 500
    return jsonify(data)

@app.route('/api/refresh')
@require_auth
def api_refresh():
    cache['data'] = None
    cache['timestamp'] = None
    data = get_alpaca_data()
    if data:
        return jsonify({'success': True, 'data': data})
    return jsonify({'error': 'Refresh failed'}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5002, debug=False)
