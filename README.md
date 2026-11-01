# Bitcoin Ticker

A compact Windows desktop widget for watching BTC/USD. It has a customizable background and border, resize controls, a compact price view, a chart, and a tray icon.

## Quick start (Windows)

1. Install Python 3.10 or newer.
2. In this folder, create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the app dependency:

   ```powershell
   pip install -r requirements.txt
   ```

4. Configure a compatible price-history relay by copying `btc_ticker_config.example.json` to `btc_ticker_config.json` and setting `price_url` to your relay's URL. The app expects a JSON response containing timestamped history and BTC/USD change values. Keep any provider API key on the relay server; never put it in this desktop app or repository.
5. Start the widget:

   ```powershell
   python .\main.py
   ```

The app creates user preferences under `%APPDATA%\Bitcoin Ticker`. Local configuration and preferences are excluded from Git.

## Widget controls

- Click the displayed period (for example, `24H`) to cycle through 1H, 4H, 8H, and 24H.
- Click the displayed change to switch between dollar and percentage change.
- Use **Settings** to customize colors, size, opacity, chart, and compact price-only view.

The price feed is provided by CoinMarketCap through a compatible relay. The widget displays linked provider attribution. Prices may be delayed or unavailable. This software is for information only and is not investment advice.

## Project rights

No license has been chosen for this repository. You may view the source on GitHub, but reuse and redistribution rights have not been granted.
