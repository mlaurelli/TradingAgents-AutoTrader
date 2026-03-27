"""
Dashboard Mobile Sicura per TradingAgents
- Autenticazione con username/password
- Real-time P&L e performance
- Mobile-first responsive design
- Session management sicuro
"""

import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, make_response
import requests
from dotenv import load_dotenv
import logging
from zoneinfo import ZoneInfo

# Carica .env dalla root del progetto
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Configurazione security
app = Flask(__name__)
app.secret_key = os.getenv('DASHBOARD_SECRET_KEY', secrets.token_hex(32))

# Security headers gestiti da Nginx

# Config logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Timezone
IT = ZoneInfo("Europe/Rome")

# Configurazione utenti (in produzione usa database)
USERS = {
    'admin': {
        'password_hash': hashlib.sha256('admin123!@#'.encode()).hexdigest(),
        'name': 'Admin'
    }
}

# Alpaca config
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY')
ALPACA_BASE_URL = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2').rstrip('/v2').rstrip('/')

# Cache per performance
cache = {
    'data': None,
    'timestamp': None,
    'ttl': 60  # secondi
}

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_alpaca_data():
    """Ottiene dati da Alpaca con cache."""
    now = datetime.now(IT)
    
    # Check cache
    if (cache['data'] and cache['timestamp'] and 
        (now - cache['timestamp']).total_seconds() < cache['ttl']):
        return cache['data']
    
    try:
        # Get account data
        account_url = f"{ALPACA_BASE_URL}/v2/account"
        headers = {
            'APCA-API-KEY-ID': ALPACA_API_KEY,
            'APCA-API-SECRET-KEY': ALPACA_SECRET_KEY
        }
        
        account_response = requests.get(account_url, headers=headers, timeout=10)
        account_response.raise_for_status()
        account_data = account_response.json()
        
        # Get positions
        positions_url = f"{ALPACA_BASE_URL}/v2/positions"
        positions_response = requests.get(positions_url, headers=headers, timeout=10)
        positions_response.raise_for_status()
        positions_data = positions_response.json()
        
        # Get today's orders
        orders_url = f"{ALPACA_BASE_URL}/v2/orders"
        today = now.strftime('%Y-%m-%d')
        orders_params = {'status': 'all', 'after': today}
        orders_response = requests.get(orders_url, headers=headers, params=orders_params, timeout=10)
        orders_response.raise_for_status()
        orders_data = orders_response.json()
        
        # Calculate metrics
        portfolio_value = float(account_data.get('portfolio_value', 0))
        cash = float(account_data.get('cash', 0))
        initial_capital = 100000.0
        performance_pct = ((portfolio_value - initial_capital) / initial_capital) * 100
        
        total_pnl = sum(float(pos.get('unrealized_pl', 0)) for pos in positions_data)
        
        # Process positions
        processed_positions = []
        for pos in positions_data:
            pnl = float(pos.get('unrealized_pl', 0))
            pnl_pct = float(pos.get('unrealized_plpc', 0)) * 100
            
            processed_positions.append({
                'symbol': pos.get('symbol'),
                'qty': pos.get('qty'),
                'side': pos.get('side'),
                'avg_entry_price': float(pos.get('avg_entry_price', 0)),
                'current_price': float(pos.get('current_price', 0)),
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'market_value': float(pos.get('market_value', 0))
            })
        
        # Process orders
        processed_orders = []
        for order in orders_data:
            processed_orders.append({
                'symbol': order.get('symbol'),
                'side': order.get('side'),
                'qty': order.get('qty'),
                'order_type': order.get('order_type'),
                'status': order.get('status'),
                'created_at': order.get('created_at'),
                'filled_at': order.get('filled_at'),
                'filled_qty': order.get('filled_qty'),
                'filled_avg_price': order.get('filled_avg_price')
            })
        
        data = {
            'timestamp': now.isoformat(),
            'portfolio': {
                'value': portfolio_value,
                'cash': cash,
                'buying_power': float(account_data.get('buying_power', 0)),
                'equity': float(account_data.get('equity', 0)),
                'performance_pct': performance_pct,
                'performance_abs': portfolio_value - initial_capital
            },
            'positions': processed_positions,
            'orders': processed_orders,
            'summary': {
                'total_positions': len(processed_positions),
                'total_pnl': total_pnl,
                'today_orders': len(processed_orders),
                'active_orders': len([o for o in processed_orders if o['status'] in ['new', 'partially_filled']])
            }
        }
        
        # Update cache
        cache['data'] = data
        cache['timestamp'] = now
        
        return data
        
    except Exception as e:
        logger.error(f"Error fetching Alpaca data: {e}")
        return None

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
        
        # Validate credentials
        if username in USERS:
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            if password_hash == USERS[username]['password_hash']:
                session['authenticated'] = True
                session['username'] = username
                session['login_time'] = datetime.now(IT).isoformat()
                session.permanent = True
                app.permanent_session_lifetime = timedelta(hours=24)
                return redirect(url_for('dashboard'))
        
        # Invalid credentials
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
        return render_template('error.html', message='Impossibile caricare i dati')
    
    return render_template('dashboard.html', data=data)

@app.route('/api/data')
@require_auth
def api_data():
    """API endpoint per aggiornamenti real-time."""
    data = get_alpaca_data()
    if not data:
        return jsonify({'error': 'Data unavailable'}), 500
    
    return jsonify(data)

@app.route('/api/refresh')
@require_auth
def api_refresh():
    """Forza refresh dei dati (invalida cache)."""
    cache['data'] = None
    cache['timestamp'] = None
    data = get_alpaca_data()
    
    if data:
        return jsonify({'success': True, 'data': data})
    return jsonify({'error': 'Refresh failed'}), 500

# Security headers
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    return response

if __name__ == '__main__':
    # In produzione usa gunicorn
    app.run(host='127.0.0.1', port=5002, debug=False)
