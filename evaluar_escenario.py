"""
Evalua la solucion final en un escenario de sensibilidad, con los MISMOS vuelos (protocolo rapido).
Reasigna la filial de cada vuelo y la carga con los datos del escenario (warm_start_milp.resolver_carga_interchange),
valida con src/validador.py, exporta los resultados y agrega una fila a data/results/sensibilidad/resumen.csv.

Uso, desde la raiz del repositorio (rama milp-completo-warm-start):
    py evaluar_escenario.py --escenario combustible_+20
    py evaluar_escenario.py --escenario base --borde base        (borde de la semana estricto)
    py evaluar_escenario.py --escenario base --ttr 3             (tiempo minimo de transbordo de 3 h)

--escenario es una carpeta creada con crear_escenario.py (data/escenarios/<nombre>); 'raw' usa los datos originales.
"""
import argparse, csv, os, pickle, sys, time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
ap = argparse.ArgumentParser(description='Evalua la solucion final en un escenario, con los mismos vuelos.')
ap.add_argument('--escenario', required=True)
ap.add_argument('--corrida', default=os.path.join('data', 'results', 'RamaB_interchange_continua'))
ap.add_argument('--borde', choices=('flexible', 'base'), default='flexible')
ap.add_argument('--ttr', type=float, default=2.0, help='tiempo minimo de transbordo (h)')
ap.add_argument('--etiqueta', default=None, help='nombre de la fila y de la carpeta de salida')
a = ap.parse_args()

if a.escenario != 'raw':
    ruta = os.path.join('data', 'escenarios', a.escenario)
    if not os.path.isdir(ruta):
        sys.exit(f'No existe {ruta}: creelo antes con crear_escenario.py')
    os.environ['RAW'] = os.path.abspath(ruta)          # antes de importar los modulos del modelo
os.environ['BORDE'] = a.borde
os.environ['INTERCHANGE'] = '1'

import warm_start_milp as ws, gurobi_model as gm      # noqa: E402

etiqueta = a.etiqueta or (a.escenario + ('' if a.borde == 'flexible' else '_borde_base')
                          + ('' if a.ttr == 2.0 else f'_ttr{a.ttr:g}'))
salida = os.path.join('data', 'results', 'sensibilidad', etiqueta)
t0 = time.time()
it = pickle.load(open(os.path.join(a.corrida, 'modelo_resuelto.pkl'), 'rb'))['itinerario']
datos = gm.cargar_datos()
multi, val = ws.resolver_carga_interchange(it, a.ttr, pasos=3)
if not val['ok']:
    print(f'[!] La solucion no es factible en este escenario: {val["violaciones"][:5]}')
    sys.exit(1)
info = {'modelo': 'Evaluacion con los mismos vuelos (sensibilidad)', 'estado': 'Vuelos fijos',
        'corrida_start': os.path.basename(a.corrida), 'tiempo_limite_s': None, 'aviones_libres': 'ninguno',
        'escenario': a.escenario, 'borde': a.borde, 'ttr_h': a.ttr}
k = ws.exportar(salida, it, multi, val, datos, info, a.ttr)

fila = {'etiqueta': etiqueta, 'escenario': a.escenario, 'borde': a.borde, 'ttr_h': a.ttr,
        'margen_usd': round(k['margen_usd']), 'ingresos_usd': round(k['ingresos_usd']),
        'costo_vuelos_usd': round(k['costo_vuelos_usd']), 'costo_handling_usd': round(k['costo_handling_usd']),
        'toneladas': round(k['toneladas'], 1), 'pct_demanda': round(k['pct_demanda'], 2),
        'vuelos_vacios': k['vuelos_vacios'], 'interchanges': k.get('interchanges'),
        'vuelos_otra_filial': k.get('vuelos_otra_filial'), 'factor_ocupacion_pct': round(k['factor_ocupacion_pct'], 2),
        'minutos': round((time.time() - t0) / 60, 1)}
resumen = os.path.join('data', 'results', 'sensibilidad', 'resumen.csv')
nuevo = not os.path.exists(resumen)
with open(resumen, 'a', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(fila))
    if nuevo:
        w.writeheader()
    w.writerow(fila)
print(f'{etiqueta}: margen validado {fila["margen_usd"]:,} USD | demanda {fila["pct_demanda"]} % | '
      f'{fila["interchanges"]} interchanges | {fila["minutos"]} min | resultados en {salida}')
