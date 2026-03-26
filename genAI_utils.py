from datetime import datetime
import logging
import os
import io
import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv

logger = logging.getLogger('uvicorn.error')
logger.setLevel(logging.DEBUG)

# Load local .env file if it exists or pull from the environment variables.
load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_KEY:
    logger.error("¡ERROR: La variable GEMINI_API_KEY no está configurada!")

# Initialize Gemini Client
client = genai.Client(api_key=GEMINI_KEY)


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

async def get_gemini_response(prompt: str):
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        # model="gemini-2.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            # max_output_tokens=2048, # Limit the response to ~600 words
            temperature=0.1,       # Higher = more creative, Lower = more factual
        )
    )

    return response