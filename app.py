from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
import yfinance as yf
from datetime import datetime, date
import logging
import requests
from time import strftime, localtime

app = FastAPI(title="STG Finance API", description="Unofficial Finance API pulling data from Yahoo (via yfinance) & MorningStar", version="2.1.0")

logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)


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

def _safe_get(d, key, default=None):
    if isinstance(d, dict):
        return d.get(key, default)
    return default

def _get_yf_info(ticker: str) -> Dict[str, Any]:
    t = yf.Ticker(ticker)
    # fast_info is preferred when available
    fi = getattr(t, "fast_info", {}) or {}
    info = getattr(t, "info", {}) or {}
    recommendations = t.recommendations

    data = {
        "symbol": ticker,
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
        "timestamp": strftime('%Y-%m-%d %H:%M:%S', localtime(_safe_get(info, "regularMarketTime"))),
        "targetHighPrice": _safe_float(_safe_get(info, "targetHighPrice")),
        "targetLowPrice": _safe_float(_safe_get(info, "targetLowPrice")),
        "targetMeanPrice": _safe_float(_safe_get(info, "targetMeanPrice")),
        "source": "YF",
        "recommendations": recommendations.reset_index().to_dict(orient="records") if recommendations is not None and not recommendations.empty else []
    }

    return data

def _get_yf_history(ticker: str, start_date = "2000-01-01"):
    yfTicker = ticker

    t = yf.Ticker(yfTicker)
    hist = t.history(start=start_date, end=datetime.today().strftime('%Y-%m-%d'), interval="1d", auto_adjust=False)

    records = []
    for dt, row in hist.iterrows():
        records.append({
            "date": str(dt.date()),
            "close": _safe_float(row.get("Close"))
        })

    return records

def _get_ms_info(ticker: str) -> Dict[str, Any]:
    ms_code = ticker

    if ms_code is None:
        raise ValueError("No Morningstar code found for ISIN " + ticker)

    url = 'https://api-global.morningstar.com/sal-service/v1/fund/quote/v7/' +  ms_code + '/data?fundServCode=&showAnalystRatingChinaFund=false&showAnalystRating=false&hideesg=false&region=EEA&languageId=es&locale=es&clientId=MDC&benchmarkId=mstarorcat&component=sal-mip-investment-overview&version=4.69.0'
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Apikey": "lstzFDEOhfFNMLikKa0am9mgEKLBl49T", "referer": "https://global.morningstar.com/es/inversiones/fondos/" +  ticker + "/cotizacion"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()

    response = r.json()

    price = response['latestPrice']
    change_pct = response['trailing1DayReturn']
    change = round(price - (price / (1 + (change_pct / 100))), 4) if price is not None and change_pct is not None else None
    asof_date = response['latestPriceDate']
    exchange = response['domicileCountryId']

    return _output_quote(ticker, exchange, price, change, change_pct, asof_date, "MS")

def _get_ms_history(ticker: str, start_date = "2000-01-01"):
    ms_code = ticker
    
    if ms_code is None:
        raise ValueError("No Morningstar code found for ISIN " + ticker)

    url = 'https://tools.morningstar.es/api/rest.svc/timeseries_price/t92wz0sj7c?currencyId=EUR&idtype=Morningstar&frequency=daily&outputType=JSON&startDate=' + start_date + '&id=' +  ms_code + ']2]0]'
    logger.debug(url)
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()

    response = r.json()

    data = response["TimeSeries"]["Security"][0]["HistoryDetail"]

    records = []
    for row in data:
        records.append({
            "date": str(row["EndDate"]),
            "close": _safe_float(row["Value"])
        })

    return records

def _output_quote(symbol: str, exchange: str, price: Optional[float], change: Optional[float], change_pct: Optional[float], asof_date: Optional[date], source: str) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "exchange": exchange,
        "price": price,
        "change": change,
        "changesPercentage": change_pct,
        "dayLow": price,
        "dayHigh": price,
        "yearHigh": 0,
        "yearLow": 0,
        "open": price,
        "previousClose": price - change if price is not None and change is not None else None,
        "timestamp": asof_date,
        "source": source
    }

@app.get("/")
def root():
    return {
        "service": "Unofficial Finance API (Yahoo via yfinance & markets.ft.com)",
        "endpoints": ["/quote?symbols=IBE.MC,0P0000OQPB.IR", "/history?ticker=IBE.MC&start=2010-01-01"],
        "notes": [
            "Quotes for stocks/ETFs are usually delayed ~15 minutes depending on exchange.",
            "Mutual funds typically have one NAV per day.",
        ]
    }

@app.get("/quote")
def get_quote(symbols: str = Query(..., description="Comma-separated list")):
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
        logger.debug(ticker)
        
        if len(ticker) == 10:
            order = ["MS"]
        else:
            order = ["YF"]

        out = None
        errs = []
        for provider in order:
            try:
                if provider == "YF":
                    logger.debug('Entra en Yahoo')
                    out = _get_yf_info(ticker)
                    break
                elif provider == "MS":
                    logger.debug('Entra en MS')
                    out = _get_ms_info(ticker)
                    break
                else:
                    continue
            except Exception as e:
                errs.append(f"{provider} failed: {e}")
        if out is None:
            results.append({"symbol": ticker, "error": "; ".join(errs)})
        else:
            results.append(out)
    return results


@app.get("/history")
def get_history(
    ticker: str = Query(..., description="Ticker symbol, e.g. IBE.MC"),
):
    """
    Returns daily historical OHLCV for as long as Yahoo has it (bounded by start/end if provided).
    {
      symbol: "...",
      history: [{ date, close }...]
    }
    """

    if len(ticker) == 10:
        order = ["MS"]
    else:
        order = ["YF"]

    out = None
    errs = []
    for provider in order:
        try:
            if provider == "YF":
                logger.debug('Entra en Yahoo')
                out = _get_yf_history(ticker)
                break
            elif provider == "MS":
                logger.debug('Entra en MS')
                out = _get_ms_history(ticker)
                break
            else:
                continue
        except Exception as e:
            errs.append(f"{provider} failed: {e}")
        
    if out is None:
        return JSONResponse(status_code=500, content={"error": "; ".join(errs), "symbol": ticker})
    else:
        return {"symbol": ticker, "historical": out}

@app.get("/trendhistory")
def get_history(
    tickers: str = Query(..., description="Ticker symbols and start dates, e.g. ACN@@2022-01-01,AMZ@@2025-06-01"),
):
    """
    Returns daily historical OHLCV since the provided start date for all stocks requested.
    {
      symbol: "...",
      history: [{ date, close }...]
    }
    """

    tickerList = tickers.split(',')

    if len(tickerList) == 0:
        return JSONResponse(status_code=400, content={"error": "No tickers provided"})
    else:
        out = []
        errs = []

        for tickerElement in tickerList:
            logger.debug(tickerElement)
            parts = tickerElement.split('@@')
            ticker = parts[0]
            start_date = parts[1] if len(parts) > 1 else None

            if len(ticker) == 10:
                order = ["MS"]
            else:
                order = ["YF"]

            for provider in order:
                try:
                    if provider == "YF":
                        logger.debug('Entra en Yahoo')
                        out.append({
                                "symbol": ticker,
                                "historical": _get_yf_history(ticker, start_date)
                            })
                        break
                    elif provider == "MS":
                        logger.debug('Entra en MS')
                        out.append({
                                "symbol": ticker,
                                "historical": _get_ms_history(ticker, start_date)
                            })
                        break
                    else:
                        continue
                except Exception as e:
                    errs.append(f"{provider} failed: {e}")
            
        if out is None or len(out) == 0:
            return JSONResponse(status_code=500, content={"error": "; ".join(errs), "symbol": ticker})
        else:
            return out
      
