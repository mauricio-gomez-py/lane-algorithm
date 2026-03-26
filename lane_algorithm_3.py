"""
============================================================================
ALGORITMO DE LANE — Optimización de Ley de Corte (Cut-off Grade)
============================================================================
Datos reales del proyecto:
  - Tabla Grade/Tonnage leída desde archivo .csv
  - Parámetros económicos (imagen)

Unidades: leyes en [oz/ton], tonelaje en [Mton]

Uso:
  python lane_algorithm_3.py                          # usa tongrade_lane1.csv en la misma carpeta
  python lane_algorithm_3.py --csv mi_archivo.csv     # archivo personalizado
============================================================================
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# 1. LECTURA DE DATOS DESDE CSV
# ============================================================================

COLUMNAS_REQUERIDAS = {
    'Cut off (Oz/ton)': 'cutoff',
    'Mineral (Mton)':   'mineral_mton',
    'Au (Oz/ton)':      'au_oz_ton',
    'REM':              'rem',
}

def cargar_tabla_csv(csv_path):
    """
    Lee la tabla Grade/Tonnage desde un archivo CSV.
    Retorna un DataFrame con columnas estandarizadas:
      cutoff, mineral_mton, au_oz_ton, rem
    """
    if not os.path.isfile(csv_path):
        print(f"  ERROR: No se encontró el archivo CSV: {csv_path}")
        sys.exit(1)

    df_raw = pd.read_csv(csv_path)

    # Verificar columnas requeridas
    faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in df_raw.columns]
    if faltantes:
        print(f"  ERROR: El CSV no contiene las columnas requeridas: {faltantes}")
        print(f"  Columnas encontradas: {list(df_raw.columns)}")
        sys.exit(1)

    df = df_raw[list(COLUMNAS_REQUERIDAS.keys())].rename(columns=COLUMNAS_REQUERIDAS).copy()
    df = df.dropna().reset_index(drop=True)

    print(f"  CSV cargado: {csv_path}  ({len(df)} filas)")
    return df


# ============================================================================
# 2. PARÁMETROS ECONÓMICOS
# ============================================================================

p = 1900.0        # Precio del oro                [$/oz]
s = 45.0          # Costo de venta/refinación     [$/oz]
h = 37.0          # Costo de procesamiento        [$/ton ore]
m = 3.9           # Costo de mina                 [$/ton material (ore+waste)]
f = 8.35e6        # Costos fijos                  [$/año]
y = 0.90          # Recuperación metalúrgica       [fracción]
H = 10.0e6        # Capacidad de molienda         [ton ore/año]
d = 0.15          # Tasa de descuento             [fracción]


# ============================================================================
# 3. CAPACIDADES DERIVADAS
# ============================================================================

M = 50.0e6        # Capacidad de mina [ton material/año] — AJUSTAR
K = 1.0e9         # Capacidad de mercado [oz/año] — no limitante (no especificada)


# ============================================================================
# 4. INTERPOLACIÓN
# ============================================================================

def create_interpolators(cutoff_grades, tonnes_ore, avg_grades, rem):
    """Crea funciones interpoladas T(g), Q(g), REM(g), y Material_Total(g)."""
    mat_total_arr = tonnes_ore * (1 + rem)
    T_func   = interp1d(cutoff_grades, tonnes_ore,    kind='linear', fill_value='extrapolate')
    Q_func   = interp1d(cutoff_grades, avg_grades,    kind='linear', fill_value='extrapolate')
    REM_func = interp1d(cutoff_grades, rem,           kind='linear', fill_value='extrapolate')
    TM_func  = interp1d(cutoff_grades, mat_total_arr, kind='linear', fill_value='extrapolate')
    return T_func, Q_func, REM_func, TM_func


# ============================================================================
# 5. LEYES DE EQUILIBRIO (BREAK-EVEN)
# ============================================================================

def breakeven_grades(p, s, h, m, y, f, d, H, F_opp=0):
    """
    Leyes de equilibrio de Lane [oz/ton].

    g_mh  = ley break-even mina-molienda (sin costo de oportunidad)
    g_hk  = ley break-even molienda-mercado (con costo de oportunidad de planta)
    g_mk  = ley break-even mina-mercado

    En unidades de oz/ton (el precio ya está en $/oz).
    """
    net_revenue = (p - s) * y   # $ netos por oz de metal en el ore, corregido por recovery

    # g_mh: ley a la cual el beneficio de procesar 1 ton = costo de procesarla
    # (p - s) * y * g = h  →  g = h / [(p-s)*y]
    g_mh = h / net_revenue

    # g_hk: incluye costo de oportunidad de ocupar la planta
    # (p - s) * y * g = h + (f + F_opp)/H
    g_hk = (h + (f + F_opp) / H) / net_revenue

    # g_mk: incluye costo de mina
    # Aquí el mining cost se aplica al total material = ore*(1+REM)
    # Simplificación: g_mk = (h + m*(1+REM_promedio)) / [(p-s)*y]
    # Como REM varía, usamos una estimación conservadora
    g_mk = (h + m) / net_revenue

    return g_mh, g_hk, g_mk


# ============================================================================
# 6. ALGORITMO DE LANE — ITERACIÓN PRINCIPAL
# ============================================================================

def lane_algorithm(cutoff_grades, tonnes_ore, avg_grades, rem,
                   p, s, h, m, y, f, d, M, H, K,
                   max_iter=50, tol=1e-6):
    """
    Algoritmo iterativo de Lane para encontrar la ley de corte óptima.

    Para cada ley de corte candidata:
    1. Determina T(g), Q(g), REM(g)
    2. Calcula material total = T(g)*(1+REM(g))
    3. Determina cuál capacidad es limitante (M, H, o K)
    4. Calcula el beneficio anual y el VAN
    5. Itera sobre el costo de oportunidad F
    """
    T_func, Q_func, REM_func, TM_func = create_interpolators(
        cutoff_grades, tonnes_ore, avg_grades, rem
    )

    net_revenue = (p - s) * y  # $/oz neto por oz contenida

    # Rango de evaluación (discretización fina del cut-off)
    g_min = cutoff_grades.min()
    g_max = cutoff_grades.max()
    g_range = np.linspace(g_min, g_max, 1000)

    # ---- Break-even inicial ----
    g_mh_0, g_hk_0, g_mk_0 = breakeven_grades(p, s, h, m, y, f, d, H, 0)

    print(f"\n{'='*65}")
    print(f"  LEYES DE EQUILIBRIO (Break-Even) — F_oportunidad = 0")
    print(f"{'='*65}")
    print(f"  g_mh  (mina-molienda)     : {g_mh_0:.6f} oz/ton")
    print(f"  g_hk  (molienda-mercado)  : {g_hk_0:.6f} oz/ton")
    print(f"  g_mk  (mina-mercado)      : {g_mk_0:.6f} oz/ton")
    print(f"{'='*65}\n")

    # ---- Función de evaluación para un cut-off dado ----
    def evaluate_cutoff(g, F_opp):
        """Calcula el valor anual V para una ley de corte g y costo de oportunidad F_opp."""
        T_g = max(float(T_func(g)), 0)        # Ore tonnes above g
        Q_g = max(float(Q_func(g)), 0)        # Average grade above g [oz/ton]
        R_g = max(float(REM_func(g)), 0)       # Stripping ratio at g

        if T_g <= 0 or Q_g <= 0:
            return None

        # Metal total recuperable [oz]
        metal_oz = T_g * Q_g * y

        # Material total (ore + waste)
        mat_total = T_g * (1 + R_g)

        # ---- Determinar la capacidad limitante ----
        # Tiempo para agotar según cada capacidad:
        t_mine = mat_total / M     # años si mina limita
        t_mill = T_g / H           # años si planta limita
        t_mkt  = metal_oz / K      # años si mercado limita

        # La capacidad limitante es la que tarda MÁS (cuello de botella)
        t_life = max(t_mine, t_mill, t_mkt)

        if t_life <= 0:
            return None

        # Determinar cuál limita
        if t_life == t_mill:
            limiting = 'mill'
        elif t_life == t_mine:
            limiting = 'mine'
        else:
            limiting = 'market'

        # Flujos anuales
        ore_yr  = T_g / t_life           # ton ore / año
        mat_yr  = mat_total / t_life     # ton material / año
        met_yr  = metal_oz / t_life      # oz metal / año

        # Revenue y costos anuales
        revenue  = met_yr * (p - s)             # $/año
        cost_proc = ore_yr * h                  # costo procesamiento
        cost_mine = mat_yr * m                  # costo mina (ore + waste)
        cost_fixed = f                          # costos fijos

        profit = revenue - cost_proc - cost_mine - cost_fixed  # $/año

        # Factor de anualidad para VAN
        n = max(t_life, 0.1)
        annuity = (1 - (1 + d)**(-n)) / d
        npv = profit * annuity

        # Valor marginal (para la iteración de Lane)
        # V = profit - d * F_opp (beneficio neto del costo de oportunidad)
        V = profit - d * F_opp

        return {
            'cutoff': g,
            'ore_mt': T_g / 1e6,
            'grade': Q_g,
            'rem': R_g,
            'metal_oz': metal_oz,
            'mat_total_mt': mat_total / 1e6,
            't_life': t_life,
            'limiting': limiting,
            'revenue': revenue,
            'cost_proc': cost_proc,
            'cost_mine': cost_mine,
            'profit': profit,
            'npv': npv,
            'V': V,
            'ore_yr': ore_yr,
            'mat_yr': mat_yr,
            'met_yr': met_yr
        }

    # ---- Iteración de Lane ----
    F_opp = 0.0
    history = []
    g_opt_prev = -1.0

    print(f"{'='*80}")
    print(f"  ITERACIÓN DEL ALGORITMO DE LANE")
    print(f"{'='*80}")
    print(f"{'Iter':>5} {'g_opt(oz/t)':>12} {'Ore(Mt)':>10} {'Grade(oz/t)':>12} "
          f"{'REM':>6} {'Life(yr)':>9} {'Profit(M$)':>11} {'VAN(M$)':>10} {'Limita':>8}")
    print(f"{'-'*80}")

    for iteration in range(max_iter):

        # Evaluar todas las leyes de corte candidatas
        best_V = -np.inf
        best_result = None

        # También trackear los óptimos por capacidad limitante
        best_by_limit = {'mine': None, 'mill': None, 'market': None}
        best_V_by_limit = {'mine': -np.inf, 'mill': -np.inf, 'market': -np.inf}

        all_results = []

        for g in g_range:
            res = evaluate_cutoff(g, F_opp)
            if res is None:
                continue

            all_results.append(res)

            # Mejor global
            if res['V'] > best_V:
                best_V = res['V']
                best_result = res

            # Mejor por tipo de limitante
            lim = res['limiting']
            if res['V'] > best_V_by_limit[lim]:
                best_V_by_limit[lim] = res['V']
                best_by_limit[lim] = res

        if best_result is None:
            print("  No se encontró solución factible.")
            break

        # ---- Obtener los 3 cut-offs de Lane ----
        g_candidates = []
        for lim_type in ['mine', 'mill', 'market']:
            if best_by_limit[lim_type] is not None:
                g_candidates.append(best_by_limit[lim_type]['cutoff'])
            else:
                g_candidates.append(best_result['cutoff'])

        # Regla de Lane: el óptimo es la MEDIANA de los 3 cut-offs
        g_candidates_sorted = sorted(g_candidates)
        g_optimal_lane = g_candidates_sorted[1]  # mediana

        # Re-evaluar en el óptimo de Lane
        final_res = evaluate_cutoff(g_optimal_lane, F_opp)
        if final_res is None or final_res['V'] < best_result['V']:
            # Si la mediana no es mejor, usar el mejor global
            final_res = best_result
            g_optimal_lane = best_result['cutoff']

        r = final_res

        print(f"{iteration+1:>5} {r['cutoff']:>12.6f} {r['ore_mt']:>10.1f} "
              f"{r['grade']:>12.6f} {r['rem']:>6.2f} {r['t_life']:>9.1f} "
              f"{r['profit']/1e6:>11.2f} {r['npv']/1e6:>10.2f} {r['limiting']:>8}")

        history.append({
            'iteration': iteration + 1,
            'g_optimal': g_optimal_lane,
            'g_mine': g_candidates[0],
            'g_mill': g_candidates[1],
            'g_market': g_candidates[2],
            'ore_mt': r['ore_mt'],
            'grade': r['grade'],
            'rem': r['rem'],
            'metal_oz': r['metal_oz'],
            'life_years': r['t_life'],
            'profit_annual': r['profit'],
            'npv': r['npv'],
            'F_opp': F_opp,
            'limiting': r['limiting'],
            'all_results': all_results
        })

        # ---- Convergencia ----
        if abs(g_optimal_lane - g_opt_prev) < tol and iteration > 0:
            print(f"\n  >>> CONVERGENCIA alcanzada en iteración {iteration + 1}")
            break

        g_opt_prev = g_optimal_lane

        # ---- Actualizar costo de oportunidad ----
        F_opp = max(r['npv'], 0)

    print(f"{'='*80}\n")

    return history


# ============================================================================
# 7. ANÁLISIS DE SENSIBILIDAD (barrido de cut-off sin iteración)
# ============================================================================

def sensitivity_sweep(cutoff_grades, tonnes_ore, avg_grades, rem,
                      p, s, h, m, y, f, d, M, H, K):
    """Evalúa todas las leyes de corte posibles para la curva VAN vs cutoff."""

    T_func, Q_func, REM_func, _ = create_interpolators(
        cutoff_grades, tonnes_ore, avg_grades, rem
    )

    g_range = np.linspace(cutoff_grades.min(), cutoff_grades.max(), 500)
    results = []

    for g in g_range:
        T_g = max(float(T_func(g)), 0)
        Q_g = max(float(Q_func(g)), 0)
        R_g = max(float(REM_func(g)), 0)

        if T_g <= 0 or Q_g <= 0:
            continue

        metal_oz = T_g * Q_g * y
        mat_total = T_g * (1 + R_g)

        t_mine = mat_total / M
        t_mill = T_g / H
        t_mkt  = metal_oz / K
        t_life = max(t_mine, t_mill, t_mkt)

        if t_life <= 0:
            continue

        ore_yr = T_g / t_life
        mat_yr = mat_total / t_life
        met_yr = metal_oz / t_life

        revenue = met_yr * (p - s)
        costs = ore_yr * h + mat_yr * m + f
        profit = revenue - costs

        n = max(t_life, 0.1)
        annuity = (1 - (1 + d)**(-n)) / d
        npv = profit * annuity

        # Determinar limitante
        if t_life == t_mill:
            lim = 'Planta'
        elif t_life == t_mine:
            lim = 'Mina'
        else:
            lim = 'Mercado'

        results.append({
            'cutoff_oz_ton': g,
            'ore_mt': T_g / 1e6,
            'avg_grade': Q_g,
            'rem': R_g,
            'metal_koz': metal_oz / 1e3,
            'life_years': t_life,
            'annual_profit_m': profit / 1e6,
            'npv_m': npv / 1e6,
            'limiting': lim
        })

    return pd.DataFrame(results)


# ============================================================================
# 8. GRÁFICOS
# ============================================================================

def plot_lane_results(df_table, sens_df, history, output_path):
    """Genera panel de 4 gráficos."""

    fig, axes = plt.subplots(2, 2, figsize=(15, 11))
    fig.suptitle("Algoritmo de Lane — Optimización de Ley de Corte\n"
                 "Proyecto Au | Precio: $1,900/oz | Planta: 10 Mt/año",
                 fontsize=13, fontweight='bold', y=0.98)

    g_opt = history[-1]['g_optimal'] if history else None

    # ---- (a) Curva Grade-Tonnage ----
    ax1 = axes[0, 0]
    c_blue = '#1976D2'
    c_red  = '#D32F2F'
    c_green = '#388E3C'

    ax1.plot(df_table['cutoff'], df_table['mineral_mton'], 'o-',
             color=c_blue, markersize=4, linewidth=1.5, label='Mineral (Mt)')
    ax1.set_xlabel('Ley de Corte [oz/ton]', fontsize=10)
    ax1.set_ylabel('Mineral sobre ley de corte [Mton]', color=c_blue, fontsize=10)
    ax1.tick_params(axis='y', labelcolor=c_blue)

    ax1b = ax1.twinx()
    ax1b.plot(df_table['cutoff'], df_table['au_oz_ton'], 's-',
              color=c_red, markersize=4, linewidth=1.5, label='Au (oz/ton)')
    ax1b.set_ylabel('Ley Media Au [oz/ton]', color=c_red, fontsize=10)
    ax1b.tick_params(axis='y', labelcolor=c_red)

    if g_opt is not None:
        ax1.axvline(x=g_opt, color='green', linestyle='--', alpha=0.7, linewidth=1.5)

    ax1.set_title('(a) Curva Grade-Tonnage', fontsize=11, fontweight='bold')
    ax1.grid(True, alpha=0.3)

    # ---- (b) VAN vs Ley de Corte ----
    ax2 = axes[0, 1]
    if len(sens_df) > 0:
        ax2.plot(sens_df['cutoff_oz_ton'], sens_df['npv_m'], '-',
                 color=c_green, linewidth=2)
        ax2.fill_between(sens_df['cutoff_oz_ton'], 0, sens_df['npv_m'],
                        where=sens_df['npv_m'] > 0, alpha=0.1, color='green')

        if g_opt is not None:
            npv_opt = sens_df.loc[(sens_df['cutoff_oz_ton'] - g_opt).abs().idxmin(), 'npv_m']
            ax2.axvline(x=g_opt, color='red', linestyle='--', alpha=0.8, linewidth=1.5)
            ax2.plot(g_opt, npv_opt, 'r*', markersize=18, zorder=5)
            ax2.annotate(f'g* = {g_opt:.4f} oz/t\nVAN = {npv_opt:.1f} M$',
                        xy=(g_opt, npv_opt), xytext=(g_opt + 0.005, npv_opt * 0.85),
                        fontsize=9, fontweight='bold', color='red',
                        arrowprops=dict(arrowstyle='->', color='red', lw=1.2))

    ax2.axhline(y=0, color='gray', linestyle='-', alpha=0.4)
    ax2.set_xlabel('Ley de Corte [oz/ton]', fontsize=10)
    ax2.set_ylabel('VAN [M$]', fontsize=10)
    ax2.set_title('(b) VAN vs Ley de Corte', fontsize=11, fontweight='bold')
    ax2.grid(True, alpha=0.3)

    # ---- (c) Cash Flow Anual + Vida de Mina ----
    ax3 = axes[1, 0]
    if len(sens_df) > 0:
        ax3.plot(sens_df['cutoff_oz_ton'], sens_df['annual_profit_m'], '-',
                 color='#FF8F00', linewidth=2, label='Beneficio Anual')
        ax3.axhline(y=0, color='gray', linestyle='-', alpha=0.4)

        if g_opt is not None:
            ax3.axvline(x=g_opt, color='red', linestyle='--', alpha=0.7, linewidth=1.5)

        ax3b = ax3.twinx()
        ax3b.plot(sens_df['cutoff_oz_ton'], sens_df['life_years'], '--',
                  color='#7B1FA2', linewidth=1.5, alpha=0.7, label='Vida de Mina')
        ax3b.set_ylabel('Vida de Mina [años]', color='#7B1FA2', fontsize=10)
        ax3b.tick_params(axis='y', labelcolor='#7B1FA2')

    ax3.set_xlabel('Ley de Corte [oz/ton]', fontsize=10)
    ax3.set_ylabel('Beneficio Anual [M$/año]', color='#FF8F00', fontsize=10)
    ax3.tick_params(axis='y', labelcolor='#FF8F00')
    ax3.set_title('(c) Cash Flow Anual y Vida de Mina', fontsize=11, fontweight='bold')
    ax3.grid(True, alpha=0.3)

    # ---- (d) Convergencia + Detalle del óptimo ----
    ax4 = axes[1, 1]
    if history:
        iters = [h['iteration'] for h in history]
        g_opts = [h['g_optimal'] for h in history]
        npvs = [h['npv'] / 1e6 for h in history]

        ax4.plot(iters, g_opts, 'o-', color='#D32F2F', markersize=8,
                 linewidth=2, label='g* (oz/ton)')
        ax4.axhline(y=g_opts[-1], color='red', linestyle=':', alpha=0.5)

        ax4b = ax4.twinx()
        ax4b.bar(iters, npvs, alpha=0.3, color='#1976D2', label='VAN (M$)')
        ax4b.set_ylabel('VAN [M$]', color='#1976D2', fontsize=10)
        ax4b.tick_params(axis='y', labelcolor='#1976D2')

    ax4.set_xlabel('Iteración', fontsize=10)
    ax4.set_ylabel('Ley de Corte Óptima [oz/ton]', color='#D32F2F', fontsize=10)
    ax4.tick_params(axis='y', labelcolor='#D32F2F')
    ax4.set_title('(d) Convergencia del Algoritmo', fontsize=11, fontweight='bold')
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Gráfico guardado: {output_path}")


# ============================================================================
# 9. EJECUCIÓN PRINCIPAL
# ============================================================================

def main():
    # ---- Argumento de línea de comandos para el CSV ----
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_csv = os.path.join(script_dir, 'tongrade_lane1.csv')

    parser = argparse.ArgumentParser(description='Algoritmo de Lane — Optimización de Ley de Corte')
    parser.add_argument('--csv', default=default_csv,
                        help=f'Ruta al archivo CSV con la tabla Grade/Tonnage (default: {default_csv})')
    args = parser.parse_args()

    print(f"\n{'#'*65}")
    print(f"#  ALGORITMO DE LANE — PROYECTO DE ORO")
    print(f"{'#'*65}")

    # ---- Cargar datos desde CSV ----
    df = cargar_tabla_csv(args.csv)

    # Convertir a arrays
    cutoff_grades = df['cutoff'].values             # oz/ton
    tonnes_ore    = df['mineral_mton'].values * 1e6 # ton (de Mton a ton)
    avg_grades    = df['au_oz_ton'].values          # oz/ton
    rem           = df['rem'].values                # razón estéril/mineral

    # ---- Mostrar tabla de entrada ----
    print(f"\n{'='*65}")
    print(f"  TABLA GRADE/TONNAGE")
    print(f"{'='*65}")
    print(df.to_string(index=False))
    print(f"{'='*65}")

    # ---- Mostrar parámetros ----
    print(f"\n{'='*65}")
    print(f"  PARÁMETROS ECONÓMICOS")
    print(f"{'='*65}")
    print(f"  Precio Au             : {p:>12,.0f} $/oz")
    print(f"  Costo de venta        : {s:>12,.0f} $/oz")
    print(f"  Costo procesamiento   : {h:>12,.0f} $/ton ore")
    print(f"  Costo mina            : {m:>12,.1f} $/ton material")
    print(f"  Costos fijos          : {f/1e6:>12,.2f} M$/año")
    print(f"  Recuperación          : {y*100:>11.0f}%")
    print(f"  Capacidad molienda H  : {H/1e6:>12,.0f} Mt ore/año")
    print(f"  Capacidad mina M      : {M/1e6:>12,.0f} Mt material/año")
    print(f"  Capacidad mercado K   : {'No limitante':>12}")
    print(f"  Tasa descuento        : {d*100:>11.0f}%")

    # ---- Precio neto y ley break-even rápida ----
    net_rev = (p - s) * y
    g_be_simple = h / net_rev
    print(f"\n  Precio neto (p-s)*y   : {net_rev:>12,.2f} $/oz contenida")
    print(f"  Ley break-even simple : {g_be_simple:>12.6f} oz/ton")
    print(f"{'='*65}")

    # ---- Ejecutar Lane ----
    history = lane_algorithm(
        cutoff_grades, tonnes_ore, avg_grades, rem,
        p, s, h, m, y, f, d, M, H, K
    )

    # ---- Sensibilidad ----
    sens_df = sensitivity_sweep(
        cutoff_grades, tonnes_ore, avg_grades, rem,
        p, s, h, m, y, f, d, M, H, K
    )

    # ---- Resultados Finales ----
    if history:
        final = history[-1]
        g_opt = final['g_optimal']

        print(f"\n{'#'*65}")
        print(f"#  RESULTADOS FINALES")
        print(f"{'#'*65}")
        print(f"  Ley de corte óptima (Lane)  : {g_opt:.6f} oz/ton")
        print(f"  Tonelaje de ore             : {final['ore_mt']:,.1f} Mt")
        print(f"  Ley media del ore           : {final['grade']:.6f} oz/ton")
        print(f"  REM (estéril/mineral)       : {final['rem']:.2f}")
        print(f"  Metal contenido recuperable : {final['metal_oz']:,.0f} oz")
        print(f"  Vida de mina                : {final['life_years']:.1f} años")
        print(f"  Capacidad limitante         : {final['limiting']}")
        print(f"  Beneficio anual             : {final['profit_annual']/1e6:,.2f} M$/año")
        print(f"  VAN del proyecto            : {final['npv']/1e6:,.2f} M$")
        print(f"{'#'*65}")

        # Break-even con F_opp final
        g_mh, g_hk, g_mk = breakeven_grades(p, s, h, m, y, f, d, H, final['F_opp'])
        print(f"\n  Leyes break-even (con F_opp = {final['F_opp']/1e6:.1f} M$):")
        print(f"    g_mh = {g_mh:.6f} oz/ton")
        print(f"    g_hk = {g_hk:.6f} oz/ton")
        print(f"    g_mk = {g_mk:.6f} oz/ton")

    # ---- Gráficos ----
    plot_path = os.path.join(script_dir, 'lane_results.png')
    plot_lane_results(df, sens_df, history, plot_path)

    # ---- Exportar sensibilidad ----
    csv_out_path = os.path.join(script_dir, 'lane_sensitivity.csv')
    sens_df.to_csv(csv_out_path, index=False, float_format='%.6f')
    print(f"  Sensibilidad exportada: {csv_out_path}")

    return history, sens_df


# ============================================================================
if __name__ == '__main__':
    history, sensitivity = main()
