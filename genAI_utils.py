from datetime import datetime
import logging
import os
import io
import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv
import numpy as np
from openai import OpenAI

logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)

# Load local .env file if it exists or pull from the environment variables.
load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

async def get_ai_response(prompt: str, model: str = "gemini-2.5-flash"):
    if model.startswith("gemini"):
        response = await get_gemini_response(prompt, model)
    else:
        response = await get_openrouter_response(prompt, model)

    return response

async def get_gemini_response(prompt: str, model: str):
    if not GEMINI_KEY:
        raise ValueError("¡ERROR: La variable GEMINI_API_KEY no está configurada!")

    # Initialize Gemini Client
    client = genai.Client(api_key=GEMINI_KEY)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            # max_output_tokens=2048,   # Limit the response to ~600 words
            temperature=0.1,            # Higher = more creative, Lower = more factual
        )
    )

    return response.text

async def get_openrouter_response(prompt: str, model: str):
    if not OPENROUTER_KEY:
        raise ValueError("¡ERROR: La variable OPENROUTER_API_KEY no está configurada!")
    
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_KEY,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user","content": prompt}],
        extra_body={"reasoning": {"enabled": True}}
    )
    response = response.choices[0].message.content

    return response


#-------------------------------------------------------------------#
# Financial Analysis functions                                      #
#-------------------------------------------------------------------#
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

def getFinancesPrompts(mode, summary, user_query = None):
    if mode == "last_month":
        return getLastMonthFinancePrompt(summary)
    elif mode == "full":
        return getFullFinancePrompt(summary)
    elif mode == "custom":
        return getCustomFinancePrompt(summary, user_query)
    else:
        raise ValueError("Invalid mode. Choose from 'last_month', 'full', or 'custom'.")

def getLastMonthFinancePrompt(summary):
    # Calculamos los datos relevantes en Pandas antes de enviarlo
    filtered_df, stats_reference = get_last_month_data(summary)
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

    return system_instruction

def get_last_month_data(summary_df):
    all_months = sorted(summary_df['Mes'].unique())
    # Identificar el "Último Mes Cerrado" (el anterior al actual)
    last_closed_month = all_months[-1]
    
    # --- CÁLCULO DE ESTADÍSTICAS REALES (PANDAS) ---
    # 1. Datos del último mes
    current_month_data = summary_df[summary_df['Mes'] == last_closed_month]
    total_ingresos_current = current_month_data[current_month_data['sum'] > 0]['sum'].sum()
    total_gastos_current = abs(current_month_data[current_month_data['sum'] < 0]['sum'].sum())

    # 2. Medias 3 meses anteriores
    idx = all_months.index(last_closed_month)
    prev_3_months = all_months[max(0, idx-3) : idx] # Excluimos el actual
    prev_3_data = summary_df[summary_df['Mes'].isin(prev_3_months)]
    
    # Calculamos sumas por mes para luego hacer la media
    monthly_totals = prev_3_data.groupby('Mes')['sum'].agg(
        ingresos = lambda x: x[x > 0].sum(),
        gastos = lambda x: abs(x[x < 0].sum())
    )
    avg_ingresos_3m = monthly_totals['ingresos'].mean()
    avg_gastos_3m = monthly_totals['gastos'].mean()

    # 3. Histórico del mismo mes (Febreros anteriores, etc.)
    month_suffix = last_closed_month.split('-')[1]
    same_month_prev_years = [m for m in all_months if m.endswith(f"-{month_suffix}") and m != last_closed_month]
    hist_data = summary_df[summary_df['Mes'].isin(same_month_prev_years)]
    
    hist_monthly_totals = hist_data.groupby('Mes')['sum'].agg(
        ingresos = lambda x: x[x > 0].sum(),
        gastos = lambda x: abs(x[x < 0].sum())
    )
    avg_ingresos_hist = hist_monthly_totals['ingresos'].mean()
    avg_gastos_hist = hist_monthly_totals['gastos'].mean()

    # Creamos un string de referencia blindado
    stats_reference = f"""
    VALORES REALES CALCULADOS (Sigue estos números estrictamente):
    - Mes Analizado ({last_closed_month}): Ingresos {total_ingresos_current:,.2f}€, Gastos {total_gastos_current:,.2f}€
    - Media 3 meses anteriores: Ingresos {avg_ingresos_3m:,.2f}€, Gastos {avg_gastos_3m:,.2f}€
    - Media histórica mes {month_suffix}: Ingresos {avg_ingresos_hist:,.2f}€, Gastos {avg_gastos_hist:,.2f}€
    """

    # Devolvemos el desglose original Y las estadísticas
    relevant_data = summary_df[summary_df['Mes'].isin([last_closed_month] + prev_3_months + same_month_prev_years)]
    return relevant_data, stats_reference

def getFullFinancePrompt(summary):
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
            
    return system_instruction

def getCustomFinancePrompt(summary, user_query):
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

    return system_instruction

#-------------------------------------------------------------------#
# Portfolio Analysis functions                                      #
#-------------------------------------------------------------------#
def getPortfolioPrompts(mode, portfolio_data, user_query = None):
    if mode == "portfolio":
        return getPortfolioOverviewPrompt(portfolio_data)
    elif mode == "custom":
        return getCustomPortfolioPrompt(portfolio_data, user_query)
    else:
        raise ValueError("Invalid mode. Choose from 'overview' or 'custom'.")

def getPortfolioOverviewPrompt(portfolio_data):
    system_instruction = f"""
        Actúa como un Analista Financiero experto en el mercado global de valores. 
        Tu misión es analizar el rendimiento de la cartera de inversiones y proponer cambios relevantes en base al contexto socio-economico actual.
                    
        Te proporciono los datos de todas las inversiones actualmente en la cartera, incluyendo cada una de las compras de participaciones con su fecha y coste:
                
        CARTERA DE INVERSIONES EN FORMATO JSON:
        {portfolio_data}

        TAREAS:
        - Analiza el rendimiento de cada inversión en la cartera, teniendo en cuenta su evolución histórica y su contexto actual.
        - Compara el rendimiento de cada inversión con su benchmark de referencia (ej. IBEX35 para acciones españolas, S&P500 para americanas, etc.).
        - Detecta si alguna inversión ha tenido un rendimiento atípico o se ha desviado significativamente de su benchmark, analiza posibles causas y propón una acción concreta (mantener, vender, comprar más) con su justificación.
        - Proporciona una recomendación general sobre la salud de la cartera y si es necesario hacer ajustes para mejorar su rendimiento o reducir riesgos, teniendo en cuenta el contexto socio-económico actual (inflación, tipos de interés, situación geopolítica, etc.).
        - Si el usuario no ha proporcionado información suficiente para hacer un análisis completo, indícalo claramente y sugiere qué datos adicionales serían necesarios para un análisis más preciso.

        A TENER EN CUENTA:
        - Las inversiones en IE00BK5BQT80 (Vanguard FTSE All-World UCITS ETF USD Acc) son inversiones a futuro para cada uno de mis hijos (Naia y Unai), el resto son inversiones personales.
        - Son un inversor sin experiencia, con un perfil de inversor conservador/moderado, por lo que valoro mucho la claridad y la simplicidad en las explicaciones. Evita tecnicismos o jerga financiera sin explicación.
        - El objetivo de la cartera es obtener una rentabilidad positiva a largo plazo, pero sin asumir riesgos excesivos. Tengo aportaciones mensuales regulares para la mayoria de los fondos y ETFs, y aportaciones extraordinarias excepcionales, por lo que también valoro recomendaciones sobre cómo ajustar las aportaciones futuras en función del rendimiento actual de la cartera.
        - Las acciones, a excepción de las de Accenture, que son parte de un programa de compra de acciones para el empleado con un 15% de descuento (dos veces al año y con reducción mensual de un porcentaje de la nómina para dicha compra), son de momento pura experimentación, y mi objetivo no es mantener una inversion activa en acciones especificas sino en fondos o ETFs.
        - La cantidad total invertida es un pequeño porcentaje de mis ahorros totales, por lo que no me importa asumir pérdidas a corto plazo en alguna inversión siempre que el análisis sea riguroso y las recomendaciones estén bien justificadas.

        INSTRUCCIONES: 
        - Responde de forma breve y concisa, y evita introducciones genéricas. 
        - Basándote en los datos proporcionados y teniendo en cuenta el CONTEXTO SOCIOECONÓMICO actual.
        - Usa Markdown, emojis y un tono cercano pero profesional.
        - IDIOMA: Responde en Español.
    """

    return system_instruction

def getCustomPortfolioPrompt(portfolio_data, user_query):
    system_instruction = f"""
        Actúa como un Analista Financiero experto en el mercado global de valores que analiza la cartera de inversiones del usuario.
                
        CARTERA DE INVERSIONES EN FORMATO JSON:
        {portfolio_data}

        PREGUNTA DEL USUARIO: {user_query}
               
        INSTRUCCIONES: 
        - Responde de forma breve y concisa, basándote en los datos proporcionados y teniendo en cuenta el contexto socio-económico global.
        - Si la pregunta no tiene que ver con la cartera de valores, recuérdale amablemente que solo puedes analizar sus inversiones.
        - Usa un tono cercano pero profesional.
        - Usa Markdown, emojis y responde en español.
    """
    
    return system_instruction


# async def get_gemini_models():
#     logger.debug("Fetching available Gemini models...")
#     response = client.models.list()

#     for model in response:
#         logger.debug(f"Model: {model.name}, Display Name: {model.display_name}")

#     # logger.debug(f"Available models: {[model.name for model in response.models]}")
#     return response