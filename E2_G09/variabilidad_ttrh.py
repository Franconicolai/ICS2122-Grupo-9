"""Sensibilidad al tiempo mínimo de transbordo (TTR_H) sobre un itinerario ya generado.
Uso: python src/sensibilidad_ttr.py <ruta/itinerario_vuelos.csv>
"""
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from asignacion_carga_multitramos import asignar_carga_multitramos, RAW
from validador import validar

TTRS = [float(x) for x in os.environ.get('TTRS', '0,0.25,0.5,1,2').split(',')]
TLIM_CARGA = float(os.environ.get('TLIM_CARGA', 300))

df = pd.read_csv(sys.argv[1])
if 'pos' not in df.columns:
    df = df.sort_values(['avion', 't_dep_h'])
    df['pos'] = df.groupby('avion').cumcount() + 1

itinerario = {}
for r in df.itertuples():
    itinerario.setdefault(r.avion, []).append(
        {'pos': int(r.pos), 'tramo': (r.origen, r.destino), 't_dep': float(r.t_dep_h), 't_arr': float(r.t_arr_h)})
for legs in itinerario.values():
    legs.sort(key=lambda l: l['pos'])

filas = []
for ttr in TTRS:
    multi = asignar_carga_multitramos(itinerario, ruta_raw=RAW, ttr_h=ttr, tlim=TLIM_CARGA)
    if not multi['ok']:
        print(f'TTR_H={ttr}: sin solución ({multi["estado"]})', flush=True); continue
    cfg = {'ciclico': True, 'tat_variable': True, 'min_ton_escala': True, 'mantenimiento': True,
           'frecuencias': True, 'demanda_diaria': True, 'dist_frecuencias': False,
           'transferencias': True, 'ttr_h': ttr}
    v = validar(multi['solucion'], cfg, ruta_raw=RAW)
    e = multi['estadisticas']
    fila = {'TTR_H': ttr, 'margen_usd': round(v['margen']), 'toneladas': round(v['toneladas_entregadas'], 1),
            'transbordos': e['conexiones_transferencia_usadas'], 'entre_operadores': e['transferencias_entre_operadores'],
            'violaciones': len(v['violaciones'])}
    filas.append(fila)
    print(fila, flush=True)

print('\n' + pd.DataFrame(filas).to_string(index=False))