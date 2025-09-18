from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any
import yfinance as yf
from datetime import datetime, date, timedelta
import logging
import requests
from bs4 import BeautifulSoup
import re 
from time import strftime, localtime

app = FastAPI(title="Unofficial Finance API (Yahoo via yfinance & markets.ft.com)", version="2.0.0")

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

def _to_float(s: str):
    """Normalize numeric string (remove NBSP, spaces, thousands separators) and parse to float."""
    if s is None:
        return None
    s = str(s).replace("\u00a0", " ").replace("\u202f", " ").strip()
    # Remove currency symbols and stray characters but keep - + . , %
    # Remove spaces and thousands separators (commas and spaces)
    s = s.replace(" ", "")
    s = s.replace(",", "")
    # remove trailing % if present (caller will handle % removal for percentages)
    s = s.replace("%", "")
    try:
        return _safe_float(s)
    except Exception:
        return None

def _to_date(text: str) -> date:
    """
    Parse a text containing a date in formats:
      - DD/MM       → uses current year
      - DD/MM/YY    → two-digit year
      - DD/MM/YYYY  → four-digit year
    
    Returns:
        datetime.date object
    Raises:
        ValueError if format is invalid.
    """
    text = text.strip()
    current_year = datetime.now().year

    # Try formats one by one
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            # If year not included, set it to current year
            if fmt == "%d/%m":
                return date(current_year, parsed.month, parsed.day)
            return parsed.date()
        except ValueError:
            continue

    raise ValueError(f"Invalid date format: {text}")

def _get_yf_info(ticker: str) -> Dict[str, Any]:
    t = yf.Ticker(ticker)
    # fast_info is preferred when available
    fi = getattr(t, "fast_info", {}) or {}
    info = getattr(t, "info", {}) or {}

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
        "source": "YF"
    }

    return data

def _get_ft_info(ticker: str) -> Dict[str, Any]:
    url = 'https://markets.ft.com/data/funds/tearsheet/summary?s=' + ticker
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    price = None
    change = None
    change_pct = None
    asof_date = None

    priceSearchString = "div.mod-tearsheet-overview__quote ul.mod-tearsheet-overview__quote__bar li span.mod-ui-data-list__value"
    changeSearchString = "div.mod-tearsheet-overview__quote ul.mod-tearsheet-overview__quote__bar li span.mod-ui-data-list__value span" # .mod-format--pos
    dateSearchString = "div.mod-tearsheet-overview__quote div.mod-disclaimer"
    priceSpan = soup.select_one(priceSearchString)
    changeSpan = soup.select_one(changeSearchString)
    dateSpan = soup.select_one(dateSearchString)

    if priceSpan:
        try:
            price = _to_float(priceSpan.get_text(strip=True).replace(",", ""))
        except ValueError:
            pass

    if changeSpan:
        change_text = changeSpan.get_text(strip=True)
        # Handle multiple change formats: +0.15 (1.23%) || 2.40 / 0.82%
        m = re.match(r"([+\-]?[0-9.,]+)\s*[/(]?\s*([+\-]?[0-9.,]+)%", change_text)
        if m:
            change = _to_float(m.group(1))
            change_pct = _to_float(m.group(2))

    if dateSpan:
        text = dateSpan.get_text(" ", strip=True)
        m = re.search(r"([A-Za-z]{3,9}\s+\d{1,2}\s+\d{4})", text)
        if m:
            try:
                asof_date = datetime.strptime(m.group(1), "%b %d %Y").date()
            except ValueError:
                try:
                    asof_date = datetime.strptime(m.group(1), "%B %d %Y").date()
                except ValueError:
                    pass

    if asof_date is None and price is None and change is None:
        raise ValueError("No data extracted from FT page")

    return _output_quote(ticker, ticker[:2], price, change, change_pct, asof_date, "FT")

def _get_ms_code(isin: str, field: str) -> Optional[str]:
    url = 'https://global.morningstar.com/api/v1/es/search/securities?limit=1&page=1&query=((isin+~%3D+%22' + isin + '%22)+AND+((((investmentType+%3D+%22FE%22)+AND+(exchangeCountry+in+(%22AUT%22,%22BEL%22,%22CHE%22,%22DEU%22,%22ESP%22,%22FRA%22,%22GBR%22,%22IRL%22,%22ITA%22,%22LUX%22,%22NLD%22,%22PRT%22,%22DNK%22,%22FIN%22,%22NOR%22,%22SWE%22)))+OR+((investmentType+%3D+%22FO%22)+AND+(countriesOfSale+%3D+%22ESP%22))+OR+((investmentType+%3D+%22FV%22)+AND+(countriesOfSale+%3D+%22ESP%22)))))&sort=_score'
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    response = r.json()

    results = response.get('results', [])
    if results and len(results) > 0:
        return results[0].get('meta').get(field) #'securityID'
    else:
        return None

def _get_ms_info(ticker: str) -> Dict[str, Any]:
    ms_code = _get_ms_code(ticker, 'securityID')
    logger.debug("getting ms code")
    logger.debug(ms_code)

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

def _get_inv_info(ticker: str) -> Dict[str, Any]:
    url = 'https://es.investing.com/funds/' + ticker.lower()
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    price = None
    change = None
    change_pct = None
    asof_date = None
    exchange = None

    # Select price & change spans
    exchangeSearchString = "div.instrumentHead div.exchangeDropdownContainer a i"
    searchString = "div.instrumentDataDetails div.current-data div.main-current-data div.top span"
    dateSearchString = "div.instrumentDataDetails div.current-data div.bottom span"
    priceSpans = soup.select(searchString)
    dateSpans = soup.select(dateSearchString)
    exchangeSpan = soup.select_one(exchangeSearchString)

    if exchangeSpan:
        exchange = exchangeSpan.get_text(strip=True)
        
    if priceSpans and priceSpans[0]:
        try:
            price = _to_float(priceSpans[0].get_text(strip=True).replace(",", "."))
        except ValueError:
            pass

    if priceSpans and priceSpans[1]:
        try:
            change = _to_float(priceSpans[1].get_text(strip=True).replace(",", "."))
        except ValueError:
            pass

    if priceSpans and priceSpans[3]:
        try:
            change_pct = _to_float(priceSpans[3].get_text(strip=True).replace(",", "."))
        except ValueError:
            pass
    
    if dateSpans and dateSpans[1]:
        try:
            asof_date = _to_date(dateSpans[1].get_text(strip=True))
        except ValueError:
            pass

    if asof_date is None and price is None and change is None:
        raise ValueError("No data extracted from Investing page")

    return _output_quote(ticker, exchange, price, change, change_pct, asof_date, "INV")

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
    #parsed_symbols = _parse_symbols_param(symbols)
    tickers = [s.strip() for s in symbols.split(",") if s.strip()]
    results = []

    for ticker in tickers:
        logger.debug(ticker)
        
        if len(ticker) == 12:
            order = ["MS", "FT", "INV", "YF"]
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
                elif provider == "INV":
                    logger.debug('Entra en INV')
                    out = _get_inv_info(ticker)
                    break
                elif provider == "FT":
                    logger.debug('Entra en FT')
                    out = _get_ft_info(ticker)
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
    Uses Morningstar as fallback if no data found on Yahoo.
    """
    try:
        if (len(ticker)==12):
            yfTicker = _get_ms_code(ticker, "performanceID") + ".F"
        else:
            yfTicker = ticker

        t = yf.Ticker(yfTicker)
        hist = t.history(start="2000-01-01", end=datetime.today().strftime('%Y-%m-%d'), interval="1d", auto_adjust=False)

        records = []
        for dt, row in hist.iterrows():
            records.append({
                    "date": str(dt.date()),
                    "close": _safe_float(row.get("Close"))
                })

        return {"symbol": ticker, "historical": records}
    except Exception as e:
        logger.debug(e)
        return JSONResponse(status_code=500, content={"error": str(e), "symbol": ticker})
