#!/usr/bin/env python3
"""
HALEIO Signal Engine v2.0
EMA Trend + RSI Pullback — runs in GitHub Actions (zero dependencies).
Posts alerts to Telegram and logs signals for performance tracking.
"""

import json
import math
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ─── CONFIG ───────────────────────────────────────────────────────────────────
PAIRS = ["TAO", "NEAR", "FET"]
TIMEFRAME = "1h"
LIMIT = 250

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
RSI_LONG_TRIGGER = 40
RSI_SHORT_TRIGGER = 60
ATR_PERIOD = 14
SL_MULT = 2.5
HOLD_HOURS = 48

# Telegram (set as GitHub Secrets)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Signal log file (committed back to repo)
LOG_FILE = os.environ.get("LOG_FILE", "signal_log.json")

# State file for dedup
STATE_FILE = "signal_state.json"


# ─── KUCOIN API (works from US/GitHub Actions, unlike Binance) ─────────────────
def fetch_ohlcv(symbol: str, interval: str = "1h", limit: int = 250) -> list:
    """Fetch OHLCV from KuCoin. Returns [[ts, o, h, l, c, v], ...] (Binance-normalized)."""
    tf_map = {"1h": "1hour", "4h": "4hour", "1d": "1day"}
    kucoin_tf = tf_map.get(interval, "1hour")
    url = f"https://api.kucoin.com/api/v1/market/candles?type={kucoin_tf}&symbol={symbol}-USDT&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "HALEIO/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read())
    data = raw.get("data", [])
    # KuCoin returns [ts, open, close, high, low, volume, turnover] — newest first
    # Normalize to [ts_ms, open, high, low, close, volume] — oldest first
    result = []
    for d in reversed(data):
        result.append([
            int(d[0]) * 1000,  # seconds -> ms
            float(d[1]),       # open
            float(d[3]),       # high
            float(d[4]),       # low
            float(d[2]),       # close (KuCoin puts close before high/low!)
            float(d[5]),       # volume
        ])
    return result


# ─── INDICATORS (pure math) ──────────────────────────────────────────────────
def ema(values: list, period: int) -> list:
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def rsi(closes: list, period: int = 14) -> list:
    if len(closes) < period + 1:
        return [float("nan")] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    rsi_vals = [float("nan")] * period
    rsi_vals.append(100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l))
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rsi_vals.append(100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l))
    return rsi_vals


def atr(highs, lows, closes, period=14):
    if len(highs) < 2:
        return [float("nan")] * len(highs)
    trs = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    return ema(trs, period)


# ─── SIGNAL DETECTION ─────────────────────────────────────────────────────────
def detect(closes, highs, lows, ema_f, ema_s, rsi_v, atr_v, pair):
    n = len(closes)
    if n < 60:
        return None
    price, rsi_now, rsi_prev = closes[-1], rsi_v[-1], rsi_v[-2]
    atr_now, ef, es = atr_v[-1], ema_f[-1], ema_s[-1]
    if any(math.isnan(x) for x in [rsi_now, rsi_prev, atr_now, ef, es]):
        return None

    atr_pct = atr_now / price * 100

    if ef > es and rsi_prev >= RSI_LONG_TRIGGER and rsi_now < RSI_LONG_TRIGGER:
        sl = price - atr_now * SL_MULT
        return {
            "pair": pair, "direction": "LONG", "price": round(price, 6),
            "rsi": round(rsi_now, 1), "atr_pct": round(atr_pct, 2),
            "sl": round(sl, 6), "sl_pct": round((price - sl) / price * 100, 2),
            "hold": f"{HOLD_HOURS}h",
        }

    if ef < es and rsi_prev <= RSI_SHORT_TRIGGER and rsi_now > RSI_SHORT_TRIGGER:
        sl = price + atr_now * SL_MULT
        return {
            "pair": pair, "direction": "SHORT", "price": round(price, 6),
            "rsi": round(rsi_now, 1), "atr_pct": round(atr_pct, 2),
            "sl": round(sl, 6), "sl_pct": round((sl - price) / price * 100, 2),
            "hold": f"{HOLD_HOURS}h",
        }
    return None


def get_status(closes, ema_f, ema_s, rsi_v, atr_v, pair):
    if len(closes) < 60:
        return {"pair": pair, "status": "no_data"}
    price, rsi_now, atr_now = closes[-1], rsi_v[-1], atr_v[-1]
    ef, es = ema_f[-1], ema_s[-1]
    if any(math.isnan(x) for x in [rsi_now, atr_now, ef, es]):
        return {"pair": pair, "status": "calculating"}
    trend = "UP" if ef > es else "DOWN" if ef < es else "FLAT"
    return {"pair": pair, "price": round(price, 6), "trend": trend,
            "rsi": round(rsi_now, 1), "atr_pct": round(atr_now / price * 100, 2)}


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[SKIP TELEGRAM] {text[:100]}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            json.loads(resp.read())
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")


# ─── STATE + LOGGING ─────────────────────────────────────────────────────────
def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if "state" in path else []


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2 if "log" in path else None)


def log_signal(signal: dict):
    """Append signal to log file for performance tracking."""
    log = load_json(LOG_FILE)
    if not isinstance(log, list):
        log = []
    signal["logged_at"] = datetime.now(timezone.utc).isoformat()
    signal["status"] = "OPEN"  # Will be updated manually or by a tracker
    log.append(signal)
    save_json(LOG_FILE, log)


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    state = load_json(STATE_FILE)
    signals, statuses = [], []
    now = datetime.now(timezone.utc)

    for base in PAIRS:
        try:
            candles = fetch_ohlcv(base, TIMEFRAME, LIMIT)
            if len(candles) < 60:
                statuses.append({"pair": base, "status": "no_data"})
                continue

            closes = [c[4] for c in candles]
            highs = [c[2] for c in candles]
            lows = [c[3] for c in candles]

            # Use only CLOSED candles (drop last — it's still forming)
            # Prevents false signals from mid-candle RSI dips
            cc, hc, lc = closes[:-1], highs[:-1], lows[:-1]

            ema_f = ema(cc, EMA_FAST)
            ema_s = ema(cc, EMA_SLOW)
            rsi_v = rsi(cc, RSI_PERIOD)
            atr_v = atr(hc, lc, cc, ATR_PERIOD)

            statuses.append(get_status(cc, ema_f, ema_s, rsi_v, atr_v, base))

            sig = detect(cc, hc, lc, ema_f, ema_s, rsi_v, atr_v, base)
            if sig:
                bar_id = str(candles[-2][0])  # use closed candle's timestamp
                key = f"{base}_{sig['direction']}"
                if state.get(key) != bar_id:
                    signals.append(sig)
                    state[key] = bar_id
                    log_signal(sig)

        except Exception as e:
            statuses.append({"pair": base, "error": str(e)})

    save_json(STATE_FILE, state)

    # Send to Telegram
    ts = now.strftime("%Y-%m-%d %H:%M UTC")

    if signals:
        for s in signals:
            is_long = s["direction"] == "LONG"
            emoji = "🟢" if is_long else "🔴"
            tag = "LONG" if is_long else "SHORT"
            entry_label = "Buy" if is_long else "Sell"
            sl_label = "Stop Loss"

            # Compute TP levels in plain dollars
            atr_abs = s["price"] * s["atr_pct"] / 100  # dollar ATR
            be_trigger = round(s["price"] + (1.5 * atr_abs if is_long else -1.5 * atr_abs), 6)
            trail_trigger = round(s["price"] + (3.0 * atr_abs if is_long else -3.0 * atr_abs), 6)

            # Build clean signal message — all in dollars, no jargon
            msg = (
                f"{emoji} <b>{tag}</b> · {s['pair']}/USDT\n"
                f"\n"
                f"  Entry       ${s['price']}\n"
                f"  Stop Loss   ${s['sl']}  (risk {s['sl_pct']}%)\n"
                f"\n"
                f"  ── move stop when price hits ──\n"
                f"  Breakeven   ${be_trigger}\n"
                f"  Trail       ${trail_trigger}\n"
                f"\n"
                f"  Hold max {s['hold']}  ·  RSI {s['rsi']}\n"
                f"\n"
                f"  {ts}"
            )
            send_telegram(msg)
            print(f"SIGNAL: {tag} {s['pair']} @ ${s['price']}")
    else:
        # Status dashboard — one line per coin
        lines = [f"⚪ <b>HALEIO</b> · {ts}\n"]
        for st in statuses:
            if "error" in st:
                lines.append(f"  ⚠️ {st['pair']}  error")
            elif "status" in st:
                lines.append(f"  ⚪ {st['pair']}  {st['status']}")
            else:
                # Emoji by trend
                if st["trend"] == "UP":
                    emoji = "🟢"
                elif st["trend"] == "DOWN":
                    emoji = "🔴"
                else:
                    emoji = "⚪"

                # Highlight if near trigger zone
                rsi_str = f"RSI {st['rsi']}"
                if st["trend"] == "UP" and st["rsi"] < 45:
                    rsi_str = f"<b>RSI {st['rsi']}</b> ⚡"
                elif st["trend"] == "DOWN" and st["rsi"] > 55:
                    rsi_str = f"<b>RSI {st['rsi']}</b> ⚡"

                lines.append(f"  {emoji} {st['pair']}  ${st['price']}  {rsi_str}")

        # Add trigger reminder
        lines.append("")
        lines.append("  waiting: RSI &lt;40 ↑ or RSI &gt;60 ↓")

        send_telegram("\n".join(lines))

    # Output for GitHub Actions logs
    print(json.dumps({"signals": signals, "statuses": statuses, "timestamp": now.isoformat()}, indent=2))


if __name__ == "__main__":
    main()
