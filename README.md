# TradingBot

A Python algorithmic trading bot that uses a VWAP (Volume Weighted Average Price) mean-reversion strategy to trade SPY on Alpaca Markets.

## Strategy

The bot watches the live price of SPY against its intraday VWAP every 30 seconds:

- **BUY** — when price drops 0.1% below VWAP (mean-reversion entry)
- **SELL** — when price rises 0.1% above VWAP (take profit)
- **Kill switch** — automatically shuts down if daily losses exceed $500

## Requirements

- Python 3.8+
- [Alpaca Markets](https://alpaca.markets/) account (paper or live)
- Dependencies:
  - `alpaca-trade-api`
  - `python-dotenv`
  - `pandas`

## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/your-username/tradingbot.git
   cd tradingbot
   ```

2. Install dependencies:
   ```bash
   pip install alpaca-trade-api python-dotenv pandas
   ```

3. Copy `.env.example` to `.env` and fill in your Alpaca API credentials:
   ```bash
   cp .env.example .env
   ```

   ```
   APCA_API_KEY_ID=your_api_key
   APCA_API_SECRET_KEY=your_secret_key
   APCA_API_BASE_URL=https://paper-api.alpaca.markets
   ```

4. Run the bot:
   ```bash
   python bot.py
   ```

## Configuration

Edit the settings at the top of `bot.py`:

| Variable    | Default | Description                              |
|-------------|---------|------------------------------------------|
| `SYMBOL`    | `SPY`   | Ticker symbol to trade                   |
| `TRADE_QTY` | `5`     | Number of shares per trade               |
| `MAX_LOSS`  | `500`   | Daily loss limit in USD before shutdown  |

## Disclaimer

This bot is for educational purposes. Trading involves risk. Use paper trading to test before going live.
