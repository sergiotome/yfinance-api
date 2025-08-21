# Finance API (FastAPI + yfinance)

This is a **ready-to-deploy** microservice that exposes two endpoints backed by Yahoo Finance via `yfinance`:

- `GET /quote?symbols=IBE.MC,0P0000OQPB.IR` → unified *latest available* quote for stocks, ETFs, and mutual funds.
- `GET /history?ticker=IBE.MC&start=2010-01-01&end=2020-01-01` → daily OHLCV history for as long as Yahoo has data.

> **Note**: Yahoo Finance is an unofficial source; quotes for many exchanges are delayed (~15 minutes). Mutual funds typically have 1 NAV per day.

---

## 1) Run locally (no prior Python knowledge required)

1. **Install Python**  
   - Download and install **Python 3.11** from https://www.python.org/downloads/  
   - During install, **check** “Add Python to PATH”.

2. **Download this project** (if you're reading this in GitHub, click *Code → Download ZIP*; if you got a ZIP from ChatGPT, just unzip it).

3. **Open a terminal** in the project folder:
   - Windows: open *Command Prompt* and run `cd path\to\project`
   - macOS: open *Terminal* and run `cd /path/to/project`

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

5. **Run the server**:
   ```bash
   uvicorn app:app --reload
   ```

6. **Test in your browser**:
   - Open http://127.0.0.1:8000  
   - Example: http://127.0.0.1:8000/quote?symbols=IBE.MC,0P0000OQPB.IR  
   - Example: http://127.0.0.1:8000/history?ticker=IBE.MC&start=2010-01-01

7. **API docs**:
   - FastAPI auto-docs at http://127.0.0.1:8000/docs

---

## 2) Deploy for free (Render)

> Render free tier sleeps on inactivity but is the simplest way to host this for free.

**Option A — via GitHub (recommended)**

1. Create a **GitHub repo** (https://github.com/new). Upload all files from this project (you can drag & drop in the GitHub UI).
2. Create a **Render** account at https://render.com and click **New → Web Service**.
3. Connect your GitHub repo.
4. In *Build & Deploy settings*:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
5. Click **Create Web Service**. After deploy completes, you’ll get a public URL.  
   - Test: `https://YOUR-RENDER-URL.onrender.com/quote?symbols=IBE.MC,0P0000OQPB.IR`

**Option B — Render Blueprint** (optional)  
- You can add a `render.yaml` to auto-configure. Not strictly necessary for this app.

---

## 3) Deploy for free (Railway)

1. Create a **Railway** account at https://railway.app — click **New Project → Deploy from GitHub**.
2. Select your repository.
3. When asked for commands:
   - **Install**: `pip install -r requirements.txt`
   - **Start**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Deploy and copy the public URL.

---

## 4) Usage & Notes

### /quote
- Accepts **multiple tickers** in one call via `symbols` (comma-separated).
- Returns the same JSON schema for stocks, ETFs, and mutual funds:
  ```json
  {
    "quotes": [
      {
        "symbol": "IBE.MC",
        "name": "Iberdrola, S.A.",
        "currency": "EUR",
        "exchange": "MCE",
        "price": 11.5,
        "change": -0.05,
        "changesPercentage": -0.43,
        "dayLow": 11.37,
        "dayHigh": 11.56,
        "yearHigh": 12.34,
        "yearLow": 9.21,
        "open": 11.42,
        "previousClose": 11.55,
        "timestamp": 1724238000
      }
    ]
  }
  ```

### /history
- Returns daily OHLCV from Yahoo back to the earliest available date (or within your start/end range).

### Caveats
- Yahoo Finance is **unofficial**; expect occasional field gaps per ticker. We fall back between `fast_info` and `info` to maximize coverage.
- For heavy use, consider caching the results in a database and calling Yahoo on a schedule instead of per request.

---

## 5) Customize
- To restrict who can call the API from a browser, edit the `allow_origins` list in `app.py` (CORS settings).
- You can add more endpoints (e.g., `/intraday`) using `Ticker.history(interval="1m")` (Yahoo keeps ~30 days of 1m data).

Enjoy!
