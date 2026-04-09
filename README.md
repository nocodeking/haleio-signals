# HALEIO Signal Engine

Automated crypto trading signals — EMA Trend + RSI Pullback strategy.

## Watchlist

| Coin | Why |
|------|-----|
| TAO | AI narrative, strong uptrend |
| NEAR | Best on-chain health, 2.9M DAU |
| FET | ASI merger momentum |

## Strategy

- **Uptrend** (EMA20 > EMA50): Buy when RSI dips below 40
- **Downtrend** (EMA20 < EMA50): Sell when RSI pops above 60
- **Stop Loss**: 2.5x ATR
- **Hold**: 48 hours max

## Setup

1. Create Telegram bot via @BotFather
2. Get your chat ID (message @userinfobot)
3. Add secrets to this repo:
   - `TELEGRAM_BOT_TOKEN` — your bot token
   - `TELEGRAM_CHAT_ID` — your chat ID
4. GitHub Actions runs every hour automatically

## Files

- `signal_engine.py` — core engine (zero dependencies)
- `signal_log.json` — every signal logged here for tracking
- `.github/workflows/signals.yml` — hourly cron

## Performance

Track signals in `signal_log.json`. Each entry has:
- pair, direction, entry price, stop loss, timestamp
- status: OPEN / WIN / LOSS (update manually for now)

## Roadmap

- [x] Signal alerts via Telegram
- [x] Performance logging
- [ ] Automated P&L tracking
- [ ] Paper trading mode
- [ ] Live execution (after proving the strategy)
