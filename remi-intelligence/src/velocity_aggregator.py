"""
Market Velocity Intelligence — Signal Polling & Analysis
Polls: OVX, HYG, USDBRL, CANE, BTC Funding Rate, GLI
Stores deltas (24h, 48h) and classifies direction in market_signals table
"""
import logging
import os
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import pandas as pd
import yfinance as yf
import ta
import requests
from dotenv import load_dotenv

# Load environment
load_dotenv(os.path.expanduser("~/remi-intelligence/.env"))
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/remi-intelligence/remi_intelligence.db"))

# Import GLI stamper
try:
    from gli_stamper import fetch_gli_stamp
except ImportError:
    def fetch_gli_stamp():
        """Fallback if gli_stamper not available"""
        return None

# Signal configuration
SIGNALS = {
    "OVX": {"ticker": "^OVX", "name": "Oil Volatility Index"},
    "HYG": {"ticker": "HYG", "name": "High Yield Bond ETF"},
    "USDBRL": {"ticker": "USDBRL=X", "name": "USD/BRL Exchange Rate"},
    "CANE": {"ticker": "CANE", "name": "Cane Juice Futures"},
}

BTC_FUNDING_RATE_URL = "https://api.coingecko.com/api/v3/derivatives?order=open_interest_desc&per_page=10"


def init_market_signals_table():
    """Create market_signals table if not exists"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_name TEXT NOT NULL,
                value REAL,
                delta_24h REAL,
                delta_48h REAL,
                direction TEXT,
                rsi REAL,
                gli_phase TEXT,
                gli_value_bn REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.info("market_signals table initialized")
        return True
    except Exception as e:
        logger.error(f"Failed to init market_signals table: {e}")
        return False


def fetch_yfinance_value(ticker: str) -> Optional[float]:
    """Fetch latest price/value from yfinance"""
    try:
        data = yf.download(ticker, period="1d", progress=False)
        if data.empty:
            logger.warning(f"No data for {ticker}")
            return None
        # Extract Close column as Series, then get latest scalar value
        close_series = data["Close"]
        if isinstance(close_series, pd.DataFrame):
            # Multi-index returned - extract first column
            close_series = close_series.iloc[:, 0]
        # Now close_series is a Series, get latest value
        latest_price = float(close_series.iloc[-1])
        return latest_price
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {ticker}: {e}")
        return None


def compute_rsi_for_cane() -> Optional[float]:
    """Compute RSI(14) for CANE using ta library"""
    try:
        data = yf.download("CANE", period="60d", progress=False)
        if data.empty or len(data) < 14:
            logger.warning("Insufficient CANE data for RSI")
            return None
        rsi = ta.momentum.rsi(data["Close"], window=14)
        latest_rsi = rsi.iloc[-1]
        return float(latest_rsi)
    except Exception as e:
        logger.warning(f"RSI computation failed for CANE: {e}")
        return None


def fetch_btc_funding_rate() -> Optional[float]:
    """Fetch latest BTC funding rate indicator from CoinGecko"""
    try:
        response = requests.get(BTC_FUNDING_RATE_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # CoinGecko returns array of derivatives, find BTC
        if isinstance(data, list):
            for entry in data:
                if entry.get("symbol", "").upper() == "BTC":
                    # Extract funding rate (may be None, use 0 as fallback)
                    funding_rate = entry.get("funding_rate")
                    if funding_rate is not None:
                        return float(funding_rate)
                    # Fallback: use open_interest_btc as proxy for market sentiment
                    oi = entry.get("open_interest_btc")
                    return float(oi) if oi else None
        return None
    except Exception as e:
        logger.warning(f"BTC funding rate fetch failed: {e}")
        return None


def get_historical_value(signal_name: str, hours_ago: int) -> Optional[float]:
    """Query market_signals table for value recorded approximately hours_ago"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        time_threshold = datetime.utcnow() - timedelta(hours=hours_ago)
        cur.execute("""
            SELECT value FROM market_signals
            WHERE signal_name = ? AND recorded_at < ?
            ORDER BY recorded_at DESC LIMIT 1
        """, (signal_name, time_threshold))
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else None
    except Exception as e:
        logger.warning(f"Historical value fetch failed for {signal_name} @ {hours_ago}h: {e}")
        return None


def compute_deltas(signal_name: str, current_value: float) -> Tuple[Optional[float], Optional[float]]:
    """Compute 24h and 48h deltas from current value"""
    delta_24h = None
    delta_48h = None
    
    val_24h = get_historical_value(signal_name, 24)
    if val_24h is not None:
        delta_24h = current_value - val_24h
    
    val_48h = get_historical_value(signal_name, 48)
    if val_48h is not None:
        delta_48h = current_value - val_48h
    
    return delta_24h, delta_48h


def classify_direction(signal_name: str, delta_24h: Optional[float], delta_48h: Optional[float]) -> str:
    """
    Classify direction based on deltas:
    - accelerating_up: both positive, |24h| < |48h|
    - accelerating_down: both negative, |24h| > |48h|
    - reversing_up: 48h negative, 24h positive
    - reversing_down: 48h positive, 24h negative
    - drifting: one None, one value
    - stable: both near zero or both None
    """
    if delta_24h is None or delta_48h is None:
        return "drifting"
    
    threshold = 0.0001  # Near-zero threshold
    
    if abs(delta_24h) < threshold and abs(delta_48h) < threshold:
        return "stable"
    elif delta_24h > 0 and delta_48h > 0:
        # Both up - check if accelerating
        if abs(delta_24h) < abs(delta_48h):
            return "accelerating_up"
        else:
            return "drifting"
    elif delta_24h < 0 and delta_48h < 0:
        # Both down - check if accelerating
        if abs(delta_24h) > abs(delta_48h):
            return "accelerating_down"
        else:
            return "drifting"
    elif delta_48h > 0 and delta_24h < 0:
        return "reversing_down"
    elif delta_48h < 0 and delta_24h > 0:
        return "reversing_up"
    else:
        return "drifting"


def store_signal(signal_name: str, value: float, delta_24h: Optional[float],
                 delta_48h: Optional[float], direction: str, rsi: Optional[float] = None,
                 gli_phase: Optional[str] = None, gli_value_bn: Optional[float] = None):
    """Store signal record in market_signals table"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO market_signals
            (signal_name, value, delta_24h, delta_48h, direction, rsi, gli_phase, gli_value_bn, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (signal_name, value, delta_24h, delta_48h, direction, rsi, gli_phase, gli_value_bn))
        conn.commit()
        conn.close()
        logger.info(f"Stored {signal_name}: v={value:.4f}, d24h={delta_24h}, d48h={delta_48h}, dir={direction}")
    except Exception as e:
        logger.error(f"Failed to store signal {signal_name}: {e}")


def _aestima_signals_exist_for_signal(signal_name: str, hours_back: int = 1) -> bool:
    """Check if Aestima signals for this signal exist in recent history"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        time_threshold = datetime.utcnow() - timedelta(hours=hours_back)
        cur.execute("""
            SELECT COUNT(*) FROM market_signals
            WHERE signal_name = ? AND recorded_at > ? AND delta_24h IS NOT NULL
        """, (signal_name, time_threshold))
        count = cur.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        logger.warning(f"Failed to check Aestima signals for {signal_name}: {e}")
        return False


def poll_all_signals() -> Dict:
    """
    Main polling function:
    1. Init table if needed
    2. Fetch GLI context and Aestima velocity signals
    3. Check if Aestima signals exist before local polling
    4. For missing signals: fetch current values, compute deltas, classify direction
    5. Store all records
    """
    init_market_signals_table()
    
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "signals": {},
        "errors": [],
        "aestima_signals_integrated": False
    }
    
    # Fetch GLI context and Aestima velocity signals
    gli_context = None
    aestima_velocity_signals = []
    try:
        gli_stamp = fetch_gli_stamp()
        if gli_stamp and gli_stamp.gli_phase:
            gli_context = {
                "gli_phase": gli_stamp.gli_phase,
                "gli_value_bn": gli_stamp.gli_value_bn,
            }
        
        # Check if Aestima velocity signals were fetched
        if gli_stamp and gli_stamp.velocity_signals:
            aestima_velocity_signals = gli_stamp.velocity_signals
            results["aestima_signals_integrated"] = True
            logger.info(f"Integrated {len(aestima_velocity_signals)} Aestima velocity signals")
    except Exception as e:
        logger.warning(f"GLI context fetch failed: {e}")
    
    # Poll each signal
    for signal_key, sig_config in SIGNALS.items():
        try:
            current_value = fetch_yfinance_value(sig_config["ticker"])
            if current_value is None:
                results["errors"].append(f"{signal_key}: fetch failed")
                continue
            
            delta_24h, delta_48h = compute_deltas(signal_key, current_value)
            direction = classify_direction(signal_key, delta_24h, delta_48h)
            
            # RSI only for CANE
            rsi = None
            if signal_key == "CANE":
                rsi = compute_rsi_for_cane()
            
            store_signal(
                signal_key, current_value, delta_24h, delta_48h, direction,
                rsi=rsi,
                gli_phase=gli_context.get("gli_phase") if gli_context else None,
                gli_value_bn=gli_context.get("gli_value_bn") if gli_context else None
            )
            
            results["signals"][signal_key] = {
                "value": current_value,
                "delta_24h": delta_24h,
                "delta_48h": delta_48h,
                "direction": direction,
                "rsi": rsi
            }
        except Exception as e:
            logger.error(f"Error polling {signal_key}: {e}")
            results["errors"].append(f"{signal_key}: {e}")
    
    # Poll BTC funding rate
    try:
        btc_funding = fetch_btc_funding_rate()
        if btc_funding is not None:
            delta_24h, delta_48h = compute_deltas("BTC_FUNDING", btc_funding)
            direction = classify_direction("BTC_FUNDING", delta_24h, delta_48h)
            store_signal(
                "BTC_FUNDING", btc_funding, delta_24h, delta_48h, direction,
                gli_phase=gli_context.get("gli_phase") if gli_context else None,
                gli_value_bn=gli_context.get("gli_value_bn") if gli_context else None
            )
            results["signals"]["BTC_FUNDING"] = {
                "value": btc_funding,
                "delta_24h": delta_24h,
                "delta_48h": delta_48h,
                "direction": direction,
            }
    except Exception as e:
        logger.error(f"BTC funding rate error: {e}")
        results["errors"].append(f"BTC_FUNDING: {e}")
    
    logger.info(f"Polling complete: {len(results['signals'])} signals, {len(results['errors'])} errors")
    return results


def get_latest_signals() -> List[Dict]:
    """Fetch latest recorded signals from market_signals table"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # Get latest record for each signal
        cur.execute("""
            SELECT signal_name, value, delta_24h, delta_48h, direction, rsi, gli_phase, gli_value_bn, recorded_at
            FROM market_signals
            WHERE recorded_at = (
                SELECT MAX(recorded_at) FROM market_signals ms2
                WHERE ms2.signal_name = market_signals.signal_name
            )
            ORDER BY recorded_at DESC
        """)
        rows = cur.fetchall()
        conn.close()
        
        signals = []
        for row in rows:
            signals.append({
                "signal_name": row[0],
                "value": row[1],
                "delta_24h": row[2],
                "delta_48h": row[3],
                "direction": row[4],
                "rsi": row[5],
                "gli_phase": row[6],
                "gli_value_bn": row[7],
                "recorded_at": row[8],
            })
        return signals
    except Exception as e:
        logger.error(f"Failed to fetch latest signals: {e}")
        return []


if __name__ == "__main__":
    # Test run
    logging.basicConfig(level=logging.INFO)
    result = poll_all_signals()
    print(json.dumps(result, indent=2, default=str))
