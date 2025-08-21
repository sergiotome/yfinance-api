from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import yfinance as yf

app = FastAPI(title="Unofficial Finance API (Yahoo via yfinance)", version="1.0.0")

# Allow all origins by default (adjust if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _safe_float(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None

def _safe_int(x):
    try:
        return None if x is None else int(x)
    except Exception:
        return None

def _safe_get(d, key, default=None):
    if isinstance(d, dict):
        return d.get(key, default)
    return default

@app.get("/")
def root():
    return {
        "service": "Unofficial Finance API (Yahoo via yfinance)",
        "endpoints": ["/quote?symbols=IBE.MC,0P0000OQPB.IR", "/history?ticker=IBE.MC&start=2010-01-01"],
        "notes": [
            "Quotes for stocks/ETFs are usually delayed ~15 minutes depending on exchange.",
            "Mutual funds typically have one NAV per day.",
        ]
    }

@app.get("/quote")
def get_quote(symbols: str = Query(..., description="Comma-separated tickers, e.g. IBE.MC,0P0000OQPB.IR")):
    """
    Returns unified quote objects for multiple tickers in one call.
    Schema per item:
    {
      symbol, name, currency, exchange, price, change, changesPercentage,
      dayLow, dayHigh, yearHigh, yearLow, open, previousClose, timestamp
    }
    """
    tickers = [s.strip() for s in symbols.split(",") if s.strip()]
    results = []

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            # fast_info is preferred when available
            fi = getattr(t, "fast_info", {}) or {}
            info = getattr(t, "info", {}) or {}

            data = {
                "symbol": ticker,
                "name": _safe_get(info, "longName", _safe_get(info, "shortName")),
                "currency": _safe_get(fi, "currency", _safe_get(info, "currency")),
                "exchange": _safe_get(info, "exchange", _safe_get(info, "exchangeName")),
                "price": _safe_float(_safe_get(fi, "last_price", _safe_get(info, "regularMarketPrice"))),
                "change": _safe_float(_safe_get(info, "regularMarketChange")),
                "changesPercentage": _safe_float(_safe_get(info, "regularMarketChangePercent")),
                "dayLow": _safe_float(_safe_get(fi, "day_low", _safe_get(info, "dayLow"))),
                "dayHigh": _safe_float(_safe_get(fi, "day_high", _safe_get(info, "dayHigh"))),
                "yearHigh": _safe_float(_safe_get(fi, "year_high", _safe_get(info, "fiftyTwoWeekHigh"))),
                "yearLow": _safe_float(_safe_get(fi, "year_low", _safe_get(info, "fiftyTwoWeekLow"))),
                "open": _safe_float(_safe_get(fi, "open", _safe_get(info, "regularMarketOpen"))),
                "previousClose": _safe_float(_safe_get(fi, "previous_close", _safe_get(info, "regularMarketPreviousClose"))),
                "timestamp": _safe_int(_safe_get(info, "regularMarketTime")),
            }
            # Volume is not in your required fields; add if you want. Leaving it out per spec.
            results.append(data)
        except Exception as e:
            results.append({"symbol": ticker, "error": str(e)})

    return {"quotes": results}

@app.get("/history")
def get_history(
    ticker: str = Query(..., description="Ticker symbol, e.g. IBE.MC"),
    start: str = Query(None, description="Start date YYYY-MM-DD"),
    end: str = Query(None, description="End date YYYY-MM-DD")
):
    """
    Returns daily historical OHLCV for as long as Yahoo has it (bounded by start/end if provided).
    {
      symbol: "...",
      history: [{ date, open, high, low, close, volume }...]
    }
    """
    try:
        t = yf.Ticker(ticker)
        hist = t.history(start=start, end=end, interval="1d", auto_adjust=False)

        if hist.empty:
            return JSONResponse(status_code=404, content={"error": "No data found", "symbol": ticker})

        records = []
        for dt, row in hist.iterrows():
            records.append({
                "date": str(dt.date()),
                "close": _safe_float(row.get("Close"))
            })

        return {"symbol": ticker, "history": records}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "symbol": ticker})
