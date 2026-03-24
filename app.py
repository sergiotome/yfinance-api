from fastapi import FastAPI, Query, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
import yfinance as yf
from datetime import datetime, date
import logging
import requests
from time import strftime, localtime
import os
import io
import pandas as pd
from google import genai
from google.api_core import exceptions
from google.genai import types
from dotenv import load_dotenv
import numpy as np
#import time


app = FastAPI(title="STG Finance API", description="Unofficial Finance API pulling data from Yahoo (via yfinance) & MorningStar. Also send data for analysis to Gemini", version="3.0.2")
logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)

# Load local .env file if it exists or pull from the environment variables.
load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_KEY:
    logger.error("¡ERROR: La variable GEMINI_API_KEY no está configurada!")

# Initialize Gemini Client
client = genai.Client(api_key=GEMINI_KEY)

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
    try:
        t = yf.Ticker(ticker)
        fi = getattr(t, "fast_info", {}) or {}
        info = getattr(t, "info", {}) or {}
        recommendations = getattr(t, "recommendations", None)
        timestamp = _safe_get(info, "regularMarketTime")
        if timestamp:
            try:
                timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime(timestamp))
            except Exception:
                timestamp = None
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
            "timestamp": timestamp,
            "targetHighPrice": _safe_float(_safe_get(info, "targetHighPrice")),
            "targetLowPrice": _safe_float(_safe_get(info, "targetLowPrice")),
            "targetMeanPrice": _safe_float(_safe_get(info, "targetMeanPrice")),
            "source": "YF",
            "recommendations": recommendations.reset_index().to_dict(orient="records") if recommendations is not None and not recommendations.empty else []
        }
        return data
    except Exception as e:
        logger.error(f"Error in _get_yf_info for {ticker}: {e}")
        raise

def _get_yf_history(ticker: str, start_date = "2000-01-01"):
    try:
        t = yf.Ticker(ticker)
        # Validate and parse start_date
        if not start_date:
            start_date = "2000-01-01"
        try:
            _ = datetime.strptime(start_date, "%Y-%m-%d")
        except Exception:
            start_date = "2000-01-01"
        # Get history and generate output
        logger.debug(f"Fetching history for {ticker} until {datetime.today().strftime('%Y-%m-%d')}")
        hist = t.history(start=start_date, end=datetime.today().strftime('%Y-%m-%d'), interval="1d", auto_adjust=False)
        records = [
            {"date": str(dt.date()), "close": close}
            for dt, row in hist.iterrows()
            if (close := _safe_float(row.get("Close"))) is not None
                and close == close
                and close > 0
        ]
        return records
    except Exception as e:
        logger.error(f"Error in _get_yf_history for {ticker}: {e}")
        raise

def _get_ms_info(ticker: str) -> Dict[str, Any]:
    ms_code = ticker
    if not ms_code:
        raise ValueError(f"No Morningstar code found for ISIN {ticker}")
    url = f'https://api-global.morningstar.com/sal-service/v1/fund/quote/v7/{ms_code}/data?fundServCode=&showAnalystRatingChinaFund=false&showAnalystRating=false&hideesg=false&region=EEA&languageId=es&locale=es&clientId=MDC&benchmarkId=mstarorcat&component=sal-mip-investment-overview&version=4.69.0'
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Apikey": "lstzFDEOhfFNMLikKa0am9mgEKLBl49T", "referer": f"https://global.morningstar.com/es/inversiones/fondos/{ticker}/cotizacion"}
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        response = r.json()
        price = response.get('latestPrice')
        change_pct = response.get('trailing1DayReturn')
        change = round(price - (price / (1 + (change_pct / 100))), 4) if price is not None and change_pct is not None else None
        asof_date = response.get('latestPriceDate')
        exchange = response.get('domicileCountryId')
        return _output_quote(ticker, exchange, price, change, change_pct, asof_date, "MS")
    except Exception as e:
        logger.error(f"Error in _get_ms_info for {ticker}: {e}")
        raise

def _get_ms_history(ticker: str, start_date = "2000-01-01"):
    ms_code = ticker
    if not ms_code:
        raise ValueError(f"No Morningstar code found for ISIN {ticker}")
    if not start_date:
        start_date = "2000-01-01"
    url = f'https://tools.morningstar.es/api/rest.svc/timeseries_price/t92wz0sj7c?currencyId=EUR&idtype=Morningstar&frequency=daily&outputType=JSON&startDate={start_date}&id={ms_code}]2]0]'
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        response = r.json()
        data = response["TimeSeries"]["Security"][0]["HistoryDetail"]
        records = [
            {"date": str(row["EndDate"]), "close": _safe_float(row["Value"])}
            for row in data
        ]
        return records
    except Exception as e:
        logger.error(f"Error in _get_ms_history for {ticker}: {e}")
        raise

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
        order = ["MS"] if len(ticker) == 10 else ["YF"]
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
            except Exception as e:
                logger.error(f"{provider} failed for {ticker}: {e}")
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

    order = ["MS"] if len(ticker) == 10 else ["YF"]
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
        except Exception as e:
            logger.error(f"{provider} failed for {ticker}: {e}")
            errs.append(f"{provider} failed: {e}")
    if out is None:
        return JSONResponse(status_code=500, content={"error": "; ".join(errs), "symbol": ticker})
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
    errs = []
    for tickerElement in tickerList:
        logger.debug(tickerElement)
        parts = tickerElement.split('@@')
        ticker = parts[0]
        start_date = parts[1] if len(parts) > 1 else None
        order = ["MS"] if len(ticker) == 10 else ["YF"]
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
            except Exception as e:
                logger.error(f"{provider} failed for {ticker}: {e}")
                errs.append(f"{provider} failed: {e}")
    if not out:
        return JSONResponse(status_code=500, content={"error": "; ".join(errs)})
    return out


@app.post("/analyze-finances")
async def analyze_finances(
    file: UploadFile = File(...),
    mode: str = Form("full"),  # 'full_report', 'last_month', 'custom'
    user_query: Optional[str] = Form(None)
):
    try:
        content = await file.read()
        summary = generate_summary_data(content)
        
        if mode == "last_month":
            # Filtramos el último mes en Pandas antes de enviarlo
            context_df = get_last_month_data(summary)
            data_to_send = context_df.to_csv(index=False)
            system_instruction = f"""
                Actúa como un Economista Senior y Asesor Financiero experto en el mercado español. 
                Tu misión es auditar los gastos e ingresos del último mes teniendo en cuenta el CONTEXTO SOCIOECONÓMICO (España/Madrid).
                Te proporciono los datos de ese mes junto con dos contextos críticos:
                1. TENDENCIA RECIENTE: Los 3 meses inmediatamente anteriores.
                2. ESTACIONALIDAD: El mismo mes en años anteriores.

                DATOS MENSUALES (Mes, Categoría, Subcategoría, Total, Nº Transacciones):
                {data_to_send}

                TAREAS:
                - Compara el gasto total y por categorías del último mes vs la media de los 3 meses anteriores.
                - Compara el último mes vs el mismo mes de los 5 años anteriores (¿Hay inflación real o cambio de hábito?).
                - Detecta si este mes ha habido un ingreso atípico o un gasto que rompe la estacionalidad.

                REGLA: Sé extremadamente breve. Usa una tabla Markdown para la comparativa rápida y 3 puntos clave de análisis.
                """
        elif mode == "custom" and user_query:
            data_to_send = summary.to_csv()
            system_instruction = f"""
                Actúa como un Economista Senior y Asesor Financiero experto en el mercado español que analiza los datos financieros del usuario.
                DATOS MENSUALES (Mes, Categoría, Subcategoría, Total, Nº Transacciones): 
                {data_to_send}

                PREGUNTA DEL USUARIO: {user_query}
               
                INSTRUCCIONES: 
                - Responde de forma breve y concisa, basándote en los datos proporcionados y teniendo en cuenta el CONTEXTO SOCIOECONÓMICO (España/Madrid).
                - Si la pregunta no tiene que ver con las finanzas familiares, recuérdale amablemente que solo puedes analizar sus finanzas.
                - Usa un tono cercano pero profesional.
                """
        else: # full
            # 1. Separar Ingresos y Gastos por el signo del Importe
            # En tu summary, 'sum' es el nombre que le diste a la agregación del Importe
            ingresos_df = summary[summary['sum'] > 0].copy()
            gastos_df = summary[summary['sum'] < 0].copy()
            gastos_df['sum'] = gastos_df['sum'] * -1 # Convertimos a positivo para el análisis

            # 2. Generar Estadísticas Anuales de Referencia (La "Verdad" para Gemini)
            resumen_anual = summary.copy()
            resumen_anual['Año'] = resumen_anual['Mes'].str[:4]

            # Calculamos totales por año
            stats = resumen_anual.groupby('Año')['sum'].agg(
                Ingresos = lambda x: x[x > 0].sum(),
                Gastos = lambda x: abs(x[x < 0].sum())
            ).reset_index()

            stats['Ahorro_Neto'] = stats['Ingresos'] - stats['Gastos']
            stats['%_Ahorro'] = np.where(
                stats['Ingresos'] > 0, 
                (stats['Ahorro_Neto'] / stats['Ingresos'] * 100).round(2), 
                0
            )

            # Convertimos a string para el prompt
            anual_stats_str = stats.to_string(index=False)
            system_instruction = f"""
                Actúa como un Economista Senior y Asesor Financiero experto en el mercado español. 
                Tu objetivo es analizar estos movimientos bancarios de los últimos 5 años, pero NO de forma aislada, 
                sino poniéndolos en CONTEXTO SOCIOECONÓMICO duerante ese mismo periodo de tiempo (España/Madrid).

                ---
                TABLA DE REFERENCIA ANUAL (DATOS REALES CALCULADOS):
                {anual_stats_str}

                DESGLOSE DE INGRESOS (Mensual):
                {ingresos_df.to_csv(index=False)}

                DESGLOSE DE GASTOS (Mensual):
                {gastos_df.to_csv(index=False)}
                ---

                INSTRUCCIONES DE PRECISIÓN (Chain of Thought):
                1. Antes de mencionar cualquier cifra de ahorro o gastos, verifícala contra los datos de referencia proporcionados.
                2. Si detectas una discrepancia entre tu análisis y las datos de referencia, los datos siempre tiene la razón.
                3. Para calcular variaciones, usa la fórmula: ((Valor_Nuevo - Valor_Viejo) / Valor_Viejo). Muestra el porcentaje resultante.

                INSTRUCCIONES DE ANÁLISIS CONTEXTUAL:
                1. BENCHMARKING: Compara el gasto en 'Supermercado', 'Recibos', 'Comida', 'Ocio', 'Viaje' e hijos, con el coste de vida medio de una familia en la periferia de Madrid. ¿Es un gasto razonable por el contexto o hay ineficiencia real?
                2. AJUSTE POR INFLACIÓN: Si detectas subidas en determinadas categorías, determina si el incremento es orgánico (debido a la inflación/IPC de esos años en España) o si sugiere un cambio en el hábito de consumo.
                3. RATIOS DE SALUD: Analiza el porcentaje de ahorro real (Ingresos vs Gastos) y compáralo con la regla 50/30/20.
                4. ESTACIONALIDAD: Ten en cuenta factores como la subida de la luz en invierno o el gasto en ocio en verano en España.

                REGLAS CRÍTICAS:
                - TEMPERATURA DE DATOS: No inventes números. Si no estás seguro de un cálculo, cita el dato de la tabla.
                - No te limites a decir "gastas mucho". Di: "Tu gasto ha subido un X%, pero dado que el IPC de alimentos subió un Y%, tu consumo real está bajo control" o viceversa.
                - CONCISIÓN: Máximo 800 palabras. Evita introducciones genéricas.
                - Usa Markdown, emojis y un tono profesional pero motivador.
                - IDIOMA: Responde en Español.

                ESTRUCTURA:
                - Resumen Ejecutivo (Salud financiera vs Contexto país).
                - Análisis de Tendencias Críticas (Cruze de tus datos vs Inflación/Coste vida).
                - Alertas de Anomalías (Gastos que NO se explican por el mercado o la situación familiar).
                - 3 Recomendaciones Estratégicas de alto impacto.
                """

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            # model="gemini-2.5-flash-lite",
            contents=system_instruction,
            config=types.GenerateContentConfig(
                # max_output_tokens=2048, # Limit the response to ~600 words
                temperature=0.1,       # Higher = more creative, Lower = more factual
            )
        )

        return {"analysis": response.text}

    # except exceptions.ResourceExhausted:
    #     # This is specifically for the 429 Quota limit
    #     return JSONResponse(
    #         status_code=429, 
    #         content={"error": "QUOTA_EXCEEDED", "message": "Límite diario de IA alcanzado. Vuelve a intentarlo mañana."}
    #     )
    # except Exception as e:
    #     logger.error(f"Error general: {str(e)}")
    #     return JSONResponse(
    #         status_code=500, 
    #         content={"error": "GENERIC_ERROR", "message": "Error al procesar los datos."}
    #     )
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
            content={"error": "GENERIC_ERROR", "message": "Error al procesar los datos."}
        )



def generate_summary_data(content):
    # 1. Read ONLY the 'Movimientos' tab
    # This ignores all your other tabs
    df = pd.read_excel(io.BytesIO(content), sheet_name='Movimientos')

    # 2. Filter out 'Archivado' rows
    # We only want the detailed movements for the analysis
    df = df[df['Concepto'] != 'Archivado']

    # 3. Convert to datetime
    df['Fecha Contable'] = pd.to_datetime(df['Fecha Contable'])
    
    # 4. Obtenemos el primer día del mes actual para filtrar hasta el final del último mes
    today = datetime.now()
    first_day_current_month = datetime(today.year, today.month, 1)
    df = df[df['Fecha Contable'] < first_day_current_month]
    
    # 5. Clean and Group the data
    df['Fecha Contable'] = pd.to_datetime(df['Fecha Contable'])
    df['Mes'] = df['Fecha Contable'].dt.to_period('M').astype(str)
        
    # 6. Aggregating by Month, Group, and SubGroup
    summary = df.groupby(['Mes', 'Grupo', 'SubGrupo'])['Importe'].agg(['sum', 'count']).reset_index()

    return summary

def get_last_month_data(summary_df):
    # Supongamos que 'Mes' está en formato 'YYYY-MM'
    all_months = sorted(summary_df['Mes'].unique())
    
    # 1. Identificar el "Último Mes Cerrado" (el anterior al actual)
    last_closed_month = all_months[-1] # El último mes en la lista ordenada es el último cerrado 
    
    # 2. Obtener los 3 meses inmediatamente anteriores al cerrado
    # Ejemplo: Si el cerrado es Febrero, queremos Enero, Diciembre y Noviembre.
    idx = all_months.index(last_closed_month)
    recent_months = all_months[max(0, idx-3) : idx+1]
    
    # 3. Obtener el mismo mes de años anteriores
    # Extraemos el sufijo '-MM' (ej: '-02')
    month_suffix = last_closed_month[-3:] 
    historical_same_months = [
        m for m in all_months 
        if m.endswith(month_suffix) and m != last_closed_month
    ]

    # 4. Combinar y filtrar el DataFrame
    target_months = set(recent_months + historical_same_months)
    filtered_df = summary_df[summary_df['Mes'].isin(target_months)]
    
    return filtered_df.sort_values('Mes')