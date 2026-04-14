from fastapi import FastAPI, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Annotated, Optional
import logging
import os
import stock_utils as su
import genAI_utils as gu

app = FastAPI(title="STG Finance API", description="Unofficial Finance API pulling data from Yahoo (via yfinance) & MorningStar. Also send data for analysis to Gemini", version="3.3.0")

logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)

# Lee los orígenes permitidos desde una variable de entorno. 
# Si no existe, usamos localhost por defecto para no quedarnos bloqueados.
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "http://localhost:4200")
origins = allowed_origins_str.split(",")

# Allow all origins by default (adjust if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        # logger.debug(ticker)
        out = None
        err = None
        try:
            if len(ticker) == 10:
                # logger.debug('Entra en MS')
                out = su._get_ms_info(ticker)
            else:
                # logger.debug('Entra en Yahoo')
                out = su._get_yf_info(ticker)
        except Exception as e:
            logger.error(f"{ticker} failed: {e}")
            err = f"{ticker} failed: {e}"
        
        if out is None:
            results.append({"symbol": ticker, "error": err})
        else:
            results.append(out)

    return results


@app.get("/history")
def get_history(ticker: str = Query(..., description="Ticker symbol, e.g. IBE.MC")):
    """
    Returns daily historical OHLCV for as long as Yahoo has it (bounded by start/end if provided).
    {
      symbol: "...",
      history: [{ date, close }...]
    }
    """

    out = None
    err = None

    try:
        if len(ticker) == 10:
            # logger.debug('Entra en MS')
            out = su._get_ms_history(ticker)
        else:
            # logger.debug('Entra en Yahoo')
            out = su._get_yf_history(ticker)
    except Exception as e:
        logger.error(f"{ticker} failed: {e}")
        err = f"{ticker} failed: {e}"
    
    if out is None:
        return JSONResponse(status_code=500, content={"error": err, "symbol": ticker})
    else:
        return {"symbol": ticker, "historical": out}


@app.get("/trendhistory")
def get_trendhistory(
    tickers: str = Query(..., description="Ticker symbols and start dates, e.g. ACN@@2022-01-01,AMZ@@2025-06-01"),
):
    """
    Returns daily historical OHLCV since the provided start date for all stocks requested.
    {
      symbol: "...",
      history: [{ date, close }...]
    }
    """
    tickerList = [t for t in tickers.split(',') if t.strip()]
    if not tickerList:
        return JSONResponse(status_code=400, content={"error": "No tickers provided"})
    out = []

    for tickerElement in tickerList:
        # logger.debug(tickerElement)
        parts = tickerElement.split('@@')
        ticker = parts[0]
        start_date = parts[1] if len(parts) > 1 else None
            
        try:
            if len(ticker) == 10:
                # logger.debug('Entra en MS')
                out.append({
                    "symbol": ticker,
                    "historical": su._get_ms_history(ticker, start_date)
                })
            else:
                # logger.debug('Entra en Yahoo')
                out.append({
                    "symbol": ticker,
                    "historical": su._get_yf_history(ticker, start_date)
                })
        except Exception as e:
            logger.error(f"{ticker} failed: {e}")
            out.append({"symbol": ticker, "error": f"{ticker} failed: {e}"})

    return out


@app.post("/analyze-finances")
async def analyze_finances(
    file: UploadFile = File(...),
    mode: Annotated[str, Form()] = "full",  # 'full_report', 'last_month', 'custom'
    model: Annotated[Optional[str], Form()] = "gemini-2.5-flash", # 'gemini-2.5-flash', 'gemini-3-flash-preview', 'gemini-3.1-flash-lite-preview', 'gemini-3.1-flash-live-preview', 'gemini-2.5-flash-lite'
    user_query: Annotated[Optional[str], Form()] = None
):
    try:
        content = await file.read()
        summary = gu.generate_summary_data(content)
        system_instruction = gu.getFinancesPrompts(mode, summary, user_query)
        response = await gu.get_ai_response(system_instruction, model)

        return {"analysis": response.text}

    except Exception as e:
        error_str = str(e).upper()

        # 1. Verificamos si el error contiene el código 429 o el texto de cuota        
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "QUOTA" in error_str:
            logger.warning("Límite de cuota alcanzado (429)")
            return JSONResponse(
                status_code=429, 
                content={
                    "error": "QUOTA_EXCEEDED", 
                    "message": "Has agotado las consultas gratuitas de hoy. Gemini 2.5 Flash volverá a estar disponible mañana."
                }
            )
        
        # 2. Si no es de cuota, entonces sí es un error genérico
        logger.error(f"Error general: {str(e)}")
        return JSONResponse(
            status_code=500, 
            content={"error": "GENERIC_ERROR", "message": "Error al procesar los datos: " + e.message}
        )


@app.post("/analyze-portfolio")
async def analyze_portfolio(
    portfolio: Annotated[str, Form()],
    mode: Annotated[str, Form()] = "portfolio",  # 'portfolio', 'custom'
    model: Annotated[Optional[str], Form()] = "gemini-2.5-flash", # 'gemini-2.5-flash', 'gemini-3-flash-preview', 'gemini-3.1-flash-lite-preview', 'gemini-3.1-flash-live-preview', 'gemini-2.5-flash-lite'
    user_query: Annotated[Optional[str], Form()] = None
):
    try:
        system_instruction = gu.getPortfolioPrompts(mode, portfolio, user_query)
        response = await gu.get_ai_response(system_instruction, model)

        return {"analysis": response}

    except Exception as e:
        error_str = str(e).upper()

        # 1. Verificamos si el error contiene el código 429 o el texto de cuota        
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "QUOTA" in error_str:
            logger.warning("Límite de cuota alcanzado (429)")
            return JSONResponse(
                status_code=429, 
                content={
                    "error": "QUOTA_EXCEEDED", 
                    "message": "Has agotado las consultas gratuitas de hoy. Gemini 2.5 Flash volverá a estar disponible mañana."
                }
            )
        
        # 2. Si no es de cuota, entonces sí es un error genérico
        logger.error(f"Error general: {str(e)}")
        return JSONResponse(
            status_code=500, 
            content={"error": "GENERIC_ERROR", "message": "Error al procesar los datos: " + e.message}
        )


# @app.get("/gemini-models")
# async def gemini_models():
#     try:        
#         response = await gu.get_gemini_models()
#         return response

#     except Exception as e:
#         error_str = str(e).upper()

#         # 1. Verificamos si el error contiene el código 429 o el texto de cuota        
#         if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "QUOTA" in error_str:
#             logger.warning("Límite de cuota alcanzado (429)")
#             return JSONResponse(
#                 status_code=429, 
#                 content={
#                     "error": "QUOTA_EXCEEDED", 
#                     "message": "Has agotado las consultas gratuitas de hoy. Gemini 2.5 Flash volverá a estar disponible mañana."
#                 }
#             )
        
#         # 2. Si no es de cuota, entonces sí es un error genérico
#         logger.error(f"Error general: {str(e)}")
#         return JSONResponse(
#             status_code=500, 
#             content={"error": "GENERIC_ERROR", "message": "Error al procesar los datos: " + e.message}
#         )

