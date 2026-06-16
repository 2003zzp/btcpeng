# Onchain Dog Bot

`onchain_dog_bot.py` is a full automation framework for high-risk on-chain meme-token trading.

The default execution mode is `paper`. Live execution is intentionally routed through a webhook so private keys stay outside this repository.

## Quick Start

```powershell
copy .\onchain_dog_config.example.json .\onchain_dog_config.json
python .\onchain_dog_bot.py --once
python .\onchain_dog_bot.py
```

## Execution Modes

- `paper`: Simulates fixed-size buys and automatic exits.
- `webhook`: Sends buy/sell JSON payloads to your own wallet executor or trusted trading bot.

For webhook mode, set these in `.env`:

```env
ONCHAIN_TRADE_WEBHOOK_URL=https://your-executor.example/trade
ONCHAIN_TRADE_WEBHOOK_SECRET=optional_shared_secret
```

Then set:

```json
"execution": {
  "mode": "webhook"
}
```

## What It Automates

- Watches new pools from GeckoTerminal.
- Applies liquidity, age, volume, buy/sell ratio, FDV/liquidity, and quote-token filters.
- Scores each pool and buys only above the configured score.
- Uses a fixed USD position size.
- Manages paper or webhook positions with stop loss, staged take profit, trailing exit, no-new-high exit, max-hold exit, and liquidity-drop exit.
- Stops opening new trades after daily loss, consecutive losses, max trades, or max open positions.

## Files

- State: `data/onchain_dog_state.json`
- Event log: `data/onchain_dog_events.jsonl`
- Config: `onchain_dog_config.json`

## Important

This script does not do MEV, sandwiching, front-running, or bypass anti-bot systems. It also does not inspect all contract permissions by itself. For real-money mode, use an external executor that includes its own token-safety checks before signing swaps.
