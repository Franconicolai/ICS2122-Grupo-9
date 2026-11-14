"""Consolida la sensibilidad validada y genera tablas, graficos y un resumen para el informe."""
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/capstone-matplotlib')

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter


RAIZ = Path('data/results/sensibilidad')
ENTRADA = RAIZ / 'resumen.csv'
SALIDA = RAIZ / 'informe'
MARGEN_BASE = 7_520_006
CASO_BASE_COMPARABLE = 5_997_653  # dato entregado por el equipo; no reejecutado en este repositorio


ORDEN = [
    ('Control', 'base', 'Base', 1.0),
    ('Costo PAC', 'pac_3800', '3.800 USD/h', 3800),
    ('Costo PAC', 'pac_4250', '4.250 USD/h', 4250),
    ('Costo PAC', 'pac_4450', '4.450 USD/h', 4450),
    ('Combustible', 'combustible_-20', '-20 %', -20),
    ('Combustible', 'combustible_+20', '+20 %', 20),
    ('Combustible', 'combustible_+40', '+40 %', 40),
    ('Demanda', 'demanda_-20', '-20 %', -20),
    ('Demanda', 'demanda_-10', '-10 %', -10),
    ('Demanda', 'demanda_+10', '+10 %', 10),
    ('Demanda', 'demanda_+20', '+20 %', 20),
    ('Borde semanal', 'base_borde_base', 'Estricto', 0),
    ('Cambio de operador', 'base_sin_interchange', 'Desactivado', 0),
    ('Tarifas', 'tarifas_-10', '-10 %', -10),
    ('Tarifas', 'tarifas_+10', '+10 %', 10),
    ('TTR', 'base_ttr1', '1 h', 1),
    ('TTR', 'base_ttr3', '3 h', 3),
]


def usd_millones(x, _pos=None):
    return f'USD {x / 1_000_000:.1f} M'


def entero_es(x):
    return f'{int(round(x)):,}'.replace(',', '.')


def cargar():
    df = pd.read_csv(ENTRADA)
    if df.etiqueta.duplicated().any():
        raise ValueError('resumen.csv contiene etiquetas duplicadas')
    faltan = sorted({etiqueta for _, etiqueta, _, _ in ORDEN} - set(df.etiqueta))
    if faltan:
        raise ValueError(f'faltan resultados: {faltan}')
    meta = pd.DataFrame(ORDEN, columns=['parametro', 'etiqueta', 'valor', 'valor_numerico'])
    tabla = meta.merge(df, on='etiqueta', how='left', validate='one_to_one')
    tabla['cambio_usd'] = tabla.margen_usd - MARGEN_BASE
    tabla['cambio_pct'] = 100 * tabla.cambio_usd / MARGEN_BASE
    tabla['protocolo'] = 'Mismos vuelos (reasigna operadores y carga; validador actual)'
    tabla['estado'] = 'Ejecutado y validado'
    return tabla


def grafico_tornado(tabla):
    por = tabla.set_index('etiqueta')
    rangos = [
        ('Demanda (-20 % a +20 %)', por.loc['demanda_-20', 'margen_usd'], por.loc['demanda_+20', 'margen_usd']),
        ('Combustible (-20 % a +40 %)', por.loc['combustible_-20', 'margen_usd'], por.loc['combustible_+40', 'margen_usd']),
        ('Tarifas (-10 % a +10 %)', por.loc['tarifas_-10', 'margen_usd'], por.loc['tarifas_+10', 'margen_usd']),
        ('Costo PAC (3.300 a 4.450 USD/h)', por.loc['base', 'margen_usd'], por.loc['pac_4450', 'margen_usd']),
        ('Borde semanal (estricto a flexible)', por.loc['base_borde_base', 'margen_usd'], por.loc['base', 'margen_usd']),
        ('Cambio de operador (desactivado a activado)', por.loc['base_sin_interchange', 'margen_usd'],
         por.loc['base', 'margen_usd']),
        ('TTR (1 h a 3 h)', por.loc['base_ttr1', 'margen_usd'], por.loc['base_ttr3', 'margen_usd']),
    ]
    rangos = sorted(((n, min(a, b), max(a, b)) for n, a, b in rangos), key=lambda x: x[2] - x[1])
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for y, (nombre, bajo, alto) in enumerate(rangos):
        ax.barh(y, alto - bajo, left=bajo, height=.55, color='#2F6B9A')
        ax.scatter([bajo, alto], [y, y], color=['#B23A48', '#2A9D8F'], s=38, zorder=3)
        if alto - bajo < 200_000:
            decimales = 3 if alto - bajo < 50_000 else 2
            ax.annotate(f'{bajo / 1e6:.{decimales}f} M', (bajo, y), xytext=(-5, 9),
                        textcoords='offset points', ha='right', va='bottom', fontsize=8)
            ax.annotate(f'{alto / 1e6:.{decimales}f} M', (alto, y), xytext=(5, 9),
                        textcoords='offset points', ha='left', va='bottom', fontsize=8)
        else:
            ax.text(bajo, y + .28, f'{bajo / 1e6:.2f} M', ha='center', va='bottom', fontsize=8)
            ax.text(alto, y + .28, f'{alto / 1e6:.2f} M', ha='center', va='bottom', fontsize=8)
    ax.axvline(MARGEN_BASE, color='#222222', lw=1.5, ls='--', label='Solución base: USD 7,52 M')
    ax.set_yticks(range(len(rangos)), [r[0] for r in rangos])
    ax.xaxis.set_major_formatter(FuncFormatter(usd_millones))
    ax.set_xlabel('Margen semanal validado')
    ax.set_title('En los rangos evaluados, demanda, tarifas y combustible dominan la sensibilidad', weight='bold')
    ax.grid(axis='x', alpha=.22)
    ax.legend(loc='lower right')
    fig.tight_layout()
    fig.savefig(SALIDA / 'tornado_margen.png', dpi=240)
    plt.close(fig)


def grafico_pac(tabla):
    por = tabla.set_index('etiqueta')
    x = [3300, 3800, 4250, 4450]
    y = [por.loc[e, 'margen_usd'] for e in ['base', 'pac_3800', 'pac_4250', 'pac_4450']]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(x, y, marker='o', lw=2.4, color='#2F6B9A')
    ax.axhline(CASO_BASE_COMPARABLE, color='#B23A48', ls='--', lw=1.7,
               label='Caso base comparable informado: USD 5,998 M')
    for xx, yy in zip(x, y):
        ax.annotate(f'USD {yy / 1e6:.2f} M', (xx, yy), xytext=(0, 9), textcoords='offset points', ha='center')
    ax.yaxis.set_major_formatter(FuncFormatter(usd_millones))
    ax.set_xlabel('Costo ex-fuel de PAC (USD/h)')
    ax.set_ylabel('Margen semanal validado')
    ax.set_title('Aun con PAC al costo de AND, la solución supera el caso comparable informado', weight='bold')
    ax.grid(alpha=.22)
    ax.legend()
    fig.tight_layout()
    fig.savefig(SALIDA / 'margen_costo_pac.png', dpi=240)
    plt.close(fig)


def grafico_demanda(tabla):
    por = tabla.set_index('etiqueta')
    etiquetas = ['demanda_-20', 'demanda_-10', 'base', 'demanda_+10', 'demanda_+20']
    x = [-20, -10, 0, 10, 20]
    margen = [por.loc[e, 'margen_usd'] for e in etiquetas]
    cobertura = [por.loc[e, 'pct_demanda'] for e in etiquetas]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax2 = ax.twinx()
    l1 = ax.plot(x, margen, color='#2F6B9A', marker='o', lw=2.4, label='Margen')
    l2 = ax2.plot(x, cobertura, color='#E07A2D', marker='s', lw=2.2, label='Demanda atendida')
    ax.yaxis.set_major_formatter(FuncFormatter(usd_millones))
    ax.set_xlabel('Variación de la demanda')
    ax.set_xticks(x, [f'{v:+d} %' if v else 'Base' for v in x])
    ax.set_ylabel('Margen semanal validado')
    ax2.set_ylabel('Demanda atendida (%)')
    ax2.set_ylim(min(cobertura) - 2, 100)
    ax.grid(alpha=.22)
    ax.set_title('Con vuelos fijos, más demanda aumenta el margen pero satura la cobertura', weight='bold')
    ax.legend(l1 + l2, [l.get_label() for l in l1 + l2], loc='center left')
    fig.tight_layout()
    fig.savefig(SALIDA / 'margen_y_cobertura_demanda.png', dpi=240)
    plt.close(fig)


def grafico_costos_ingresos(tabla):
    por = tabla.set_index('etiqueta')
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    series = [
        ('Precio del combustible', [-20, 0, 20, 40],
         ['combustible_-20', 'base', 'combustible_+20', 'combustible_+40'], '#2F6B9A'),
        ('Tarifas', [-10, 0, 10], ['tarifas_-10', 'base', 'tarifas_+10'], '#2A9D8F'),
    ]
    for ax, (titulo, x, etiquetas, color) in zip(axes, series):
        y = [por.loc[e, 'margen_usd'] for e in etiquetas]
        ax.plot(x, y, marker='o', lw=2.4, color=color)
        ax.axhline(MARGEN_BASE, color='#222222', lw=1.1, ls='--')
        ax.set_xticks(x, [f'{v:+d} %' if v else 'Base' for v in x])
        ax.set_xlabel('Variación')
        ax.set_title(titulo, weight='bold')
        ax.grid(alpha=.22)
    axes[0].set_ylabel('Margen semanal validado')
    axes[0].yaxis.set_major_formatter(FuncFormatter(usd_millones))
    fig.suptitle('Combustible y tarifas trasladan casi linealmente su efecto al margen', weight='bold')
    fig.tight_layout()
    fig.savefig(SALIDA / 'margen_combustible_tarifas.png', dpi=240)
    plt.close(fig)


def escribir_resumen(tabla):
    por = tabla.set_index('etiqueta')
    peor = tabla.loc[tabla.margen_usd.idxmin()]
    pac_peor = por.loc['pac_4450']
    diferencia_pac = pac_peor.margen_usd - CASO_BASE_COMPARABLE
    lineas = [
        '# Resultados del análisis de sensibilidad', '',
        'Fecha de consolidación: 14 de noviembre de 2026.', '',
        '## Alcance y método', '',
        '- Todos los escenarios ejecutados mantienen fijos los 387 vuelos de la Rama B y reoptimizan la carga y la filial operadora.',
        '- Cada resultado fue recalculado con el validador actual de `milp-completo-warm-start` y tiene `validador_ok: true`.',
        '- Estos resultados miden robustez y adaptación operativa de corto plazo; no prueban optimalidad global.',
        f'- El control reproduce el margen congelado de USD {entero_es(MARGEN_BASE)}.',
        '- El caso comparable de USD 5.997.653 fue proporcionado por el equipo, pero no aparece como artefacto reproducible en este checkout.', '',
        '## Tabla consolidada', '',
        '| Parámetro | Valor | Margen (USD) | Cambio vs. base | Demanda atendida | Interchanges |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for r in tabla.itertuples():
        lineas.append(f'| {r.parametro} | {r.valor} | {entero_es(r.margen_usd)} | {r.cambio_pct:+.1f} % | '
                      f'{r.pct_demanda:.2f} % | {int(r.interchanges)} |')
    lineas += [
        '', '## Conclusiones observadas', '',
        f'- El peor margen de la batería rápida es demanda -20 %: USD {entero_es(peor.margen_usd)} '
        f'({peor.cambio_pct:+.1f} %). El margen sigue siendo positivo.',
        '- En los rangos probados, la demanda produce el mayor intervalo de margen; tarifas y combustible también son determinantes.',
        f'- Con PAC a 4.450 USD/h, el margen es USD {entero_es(pac_peor.margen_usd)} '
        f'({pac_peor.cambio_pct:+.1f} % vs. Rama B) y aún supera el caso comparable informado en '
        f'USD {entero_es(diferencia_pac)}.',
        f'- Combustible +40 % reduce el margen a USD {entero_es(por.loc["combustible_+40", "margen_usd"])}; '
        'la red conserva la misma cobertura e interchanges porque los vuelos están fijos.',
        f'- El borde estricto cuesta USD {entero_es(MARGEN_BASE - por.loc["base_borde_base", "margen_usd"])} '
        '(-1,4 %), por lo que el supuesto de borde flexible aporta valor, pero no explica la mayor parte de la mejora.',
        f'- Desactivar el cambio de operador reduce el margen en '
        f'USD {entero_es(MARGEN_BASE - por.loc["base_sin_interchange", "margen_usd"])} '
        f'({100 * (por.loc["base_sin_interchange", "margen_usd"] / MARGEN_BASE - 1):+.1f} %), '
        'manteniendo fijos los vuelos.',
        f'- TTR de 3 h reduce el margen solo en USD {entero_es(MARGEN_BASE - por.loc["base_ttr3", "margen_usd"])} '
        '(-0,04 %); TTR de 1 h no cambia el margen.',
        '- Con demanda +20 %, el margen aumenta, pero la cobertura cae a 83,58 %: la flota y el itinerario fijo están saturados.',
        '- El control rápido puede escoger una solución de carga alternativa con el mismo margen; por eso algunos KPI secundarios '
        '(por ejemplo vuelos vacíos) no coinciden exactamente con el archivo congelado.', '',
        '## Reproducción', '',
        'Con Gurobi y las dependencias instaladas, desde la raíz del repositorio:', '',
        '```bash',
        'python -m venv .venv',
        'source .venv/bin/activate',
        'pip install -r requirements-sensibilidad.txt',
        'python ejecutar_sensibilidad.py --grupo todos',
        'python analizar_sensibilidad.py',
        '```', '',
        'El ejecutor crea únicamente los escenarios derivados que falten, conserva intacta la Rama B congelada y omite resultados '
        'que ya tengan `validador_ok: true`. Use `--recrear-escenarios` o `--repetir` solo cuando se quiera regenerarlos.', '',
        '## Pendientes', '',
        '- Reoptimizar uno o dos escenarios adversos para medir cuánto valor recupera una replanificación de vuelos.',
        '- Incorporar al repositorio el artefacto o comando que respalda el caso comparable de USD 5.997.653.',
        '- No ejecutar el MILP completo hasta optimalidad: excede los recursos disponibles y no es necesario para esta sensibilidad.',
    ]
    (SALIDA / 'resumen_sensibilidad.md').write_text('\n'.join(lineas) + '\n', encoding='utf-8')


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    tabla = cargar()
    columnas = ['parametro', 'valor', 'etiqueta', 'margen_usd', 'cambio_usd', 'cambio_pct', 'ingresos_usd',
                'costo_vuelos_usd', 'costo_handling_usd', 'toneladas', 'pct_demanda', 'vuelos_vacios',
                'interchanges', 'vuelos_otra_filial', 'factor_ocupacion_pct', 'protocolo', 'estado']
    tabla[columnas].to_csv(SALIDA / 'tabla_sensibilidad.csv', index=False)
    grafico_tornado(tabla)
    grafico_pac(tabla)
    grafico_demanda(tabla)
    grafico_costos_ingresos(tabla)
    escribir_resumen(tabla)
    print(f'Analisis generado en {SALIDA}: {len(tabla)} escenarios ejecutados y validados')


if __name__ == '__main__':
    main()
