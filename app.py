from fastapi import FastAPI, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional
import logging
import os
import numpy as np

import stock_utils as su
import genAI_utils as gu

app = FastAPI(title="STG Finance API", description="Unofficial Finance API pulling data from Yahoo (via yfinance) & MorningStar. Also send data for analysis to Gemini", version="3.1.0")

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
    mode: str = Form("full"),  # 'full_report', 'last_month', 'custom'
    user_query: Optional[str] = Form(None)
):
    try:
        content = await file.read()
        summary = gu.generate_summary_data(content)
        
        if mode == "last_month":
            # Calculamos los datos relevantes en Pandas antes de enviarlo
            filtered_df, stats_reference = gu.get_last_month_data(summary)
            data_to_send = filtered_df.to_string()

            system_instruction = f"""
                Actúa como un Economista Senior y Asesor Financiero experto en el mercado español. 
                Tu misión es auditar los gastos e ingresos del último mes teniendo en cuenta el CONTEXTO SOCIOECONÓMICO (España/Madrid).
                Te proporciono los datos de ese mes junto con dos contextos críticos:
                1. TENDENCIA RECIENTE: Los 3 meses inmediatamente anteriores.
                2. ESTACIONALIDAD: El mismo mes en años anteriores.

                DATOS MENSUALES AGREGADOS:
                {stats_reference}

                DATOS MENSUALES POR CATEGORIA (Mes, Categoría, Subcategoría, Total, Nº Transacciones):
                {data_to_send}

                TAREAS:
                - Compara el gasto total y por categorías del último mes vs la media de los 3 meses anteriores.
                - Compara el último mes vs el mismo mes de los 5 años anteriores (¿Hay inflación real o cambio de hábito?).
                - Detecta si este mes ha habido un ingreso atípico o un gasto que rompe la estacionalidad.

                INSTRUCCIONES CRÍTICAS:
                1. Los totales de ingresos, gastos y medias DEBEN coincidir exactamente con las 'DATOS MENSUALES AGREGADOS' arriba indicadas.
                2. Utiliza los 'DATOS MENSUALES POR CATEGORIA' solo para explicar en qué categorías se ha gastado más o menos (desglose).

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

        response = await gu.get_gemini_response(system_instruction)

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
            content={"error": "GENERIC_ERROR", "message": "Error al procesar los datos."}
        )


