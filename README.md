# Binance Futures Testnet Momentum Bot

This project scans Binance USD-M Futures public market data for high-momentum USDT perpetual symbols, then submits real orders to Binance USD-M Futures Testnet when a signal triggers.

All entries are sent to Binance USD-M Futures Testnet.

## Run

```powershell
cd "C:\Users\Administrator\Documents\New project"
python .\binance_paper_momentum_bot.py
```

Run one scan only:

```powershell
python .\binance_paper_momentum_bot.py --once
```

## Crypto Signal Analyzer

If you only want a data-based direction suggestion and do not want to place orders, run:

```powershell
python .\crypto_signal_analyzer.py BTC
```

You can also input a full pair:

```powershell
python .\crypto_signal_analyzer.py SOLUSDT
```

The analyzer reads public Binance USD-M Futures data:

- Klines on `15m`, `1h`, and `4h`
- 24h ticker volume and price change
- funding rate and recent funding history
- global account long/short ratio
- top trader account and position long/short ratios
- taker buy/sell volume ratio
- open interest and open interest history
- order book bid/ask imbalance
- aggregate trades CVD and large-trade CVD
- ATR and ADX trend quality
- recent forced liquidation imbalance

Each run also appends a compact signal snapshot to:

```powershell
data\signal_history.jsonl
```

Disable signal logging:

```powershell
python .\crypto_signal_analyzer.py BTC --no-log
```

Backtest and calibrate logged signals:

```powershell
python .\backtest_signal_history.py --timeout 12
```

The backtester updates `data\signal_history.jsonl` with future returns such as `return_15m_pct`, `return_60m_pct`, and `return_240m_pct`, then prints win rate by direction and confidence bucket.

Optional onchain data uses GeckoTerminal. Add a token mapping in `config.json`:

If you have a CoinGecko API key, put it in `.env` to reduce onchain API rate limits:

```env
COINGECKO_API_KEY=your_coingecko_api_key
```

The analyzer uses the key for token pool search and token pool reads. Binance futures data does not use this key.

```json
"onchain": {
  "enabled": true,
  "symbols": {
    "EXAMPLEUSDT": {
      "network": "eth",
      "token_address": "0x..."
    }
  }
}
```

Or pass it at runtime:

```powershell
python .\crypto_signal_analyzer.py EXAMPLE --network eth --token-address 0x...
```

Save the full raw report:

```powershell
python .\crypto_signal_analyzer.py BTC --json-out data\BTCUSDT_signal.json
```

The script submits:

- MARKET entry order
- STOP_MARKET close-position stop loss
- TAKE_PROFIT_MARKET close-position take profit

## Signal Direction

The bot now uses Futures public market data:

- Tickers: `https://fapi.binance.com/fapi/v1/ticker/24hr`
- Klines: `https://fapi.binance.com/fapi/v1/klines`
- Orders: `https://testnet.binancefuture.com`

Direction is filtered by multiple timeframes:

- `1m`: short-term momentum and volume spike
- `5m`: must confirm trend direction
- `15m`: must not strongly oppose the direction

## Onchain Filter

The bot includes an optional GeckoTerminal onchain filter. It is used only as a confirmation/risk filter, not as the primary direction signal.

By default, symbols without an onchain mapping are not blocked. Add mappings in `config.json`:

```json
"onchain": {
  "enabled": true,
  "reject_on_missing_mapping": false,
  "symbols": {
    "EXAMPLEUSDT": {
      "network": "eth",
      "token_address": "0x..."
    }
  }
}
```

The first version checks:

- DEX liquidity
- DEX 24h volume
- 1h buy/sell transaction ratio

For longs, buy/sell ratio should be above `long_min_buy_sell_ratio_1h`.
For shorts, buy/sell ratio should be below `short_max_buy_sell_ratio_1h`.

## Required `.env`

Use Binance USD-M Futures Testnet keys only.

```env
BINANCE_API_KEY=your_futures_testnet_key
BINANCE_API_SECRET=your_futures_testnet_secret
BINANCE_TESTNET=true
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

## Risk Defaults

- Leverage: `3`
- Margin type: `ISOLATED`
- Margin per trade: `5%` of Futures Testnet USDT available balance
- Max position notional: disabled by default (`0`)
- Max open positions: `10`
- Stop loss: `2.5%`
- Take profit: `6%`

## Logs

- `[模式]`: confirms Binance Futures Testnet mode
- `[扫描]`: market scan progress
- `[合约持仓]`: current Binance Testnet positions
- `[候选]`: candidates selected for deeper checks
- `[信号]`: candidates that passed the score threshold
- `[合约开仓]`: Testnet order submitted
- `[状态]`: scan summary

This is not investment advice. Keep Testnet keys separate from real exchange keys.
