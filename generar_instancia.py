"""
Genera una instancia reducida del problema con un subconjunto de operadores, para estudiar métodos de resolución con
óptimo conocido antes de llevarlos a la instancia completa (que no cabe en 8 GB de RAM).

La instancia es una carpeta con los mismos 11 archivos de data/raw data, filtrados:
  - flota, mantenimiento, derechos de tráfico y costos de los operadores elegidos;
  - aeropuertos de los países donde al menos uno de ellos puede operar, y los tramos entre esos aeropuertos;
  - demanda solo de los mercados con origen y destino en esos aeropuertos, escalada para que la relación
    demanda / capacidad de la flota sea la misma que en la instancia completa (si no, unos pocos aviones
    enfrentarían toda la demanda y el problema sería otro);
  - frecuencias mínimas escaladas por la fracción de la flota (redondeadas) y solo en tramos que la flota elegida
    puede volar.
Se conserva la estructura que hace difícil al problema: transbordos entre operadores (si hay más de uno),
mercados desbalanceados, mantenimiento y frecuencias.

Uso, desde la raíz del repositorio:
    python generar_instancia.py --operadores BRA,PAC --nombre XS
    python generar_instancia.py --operadores BRA,PAC --nombre R4 --aeropuertos MIA,BOG,UIO,MDE,SCL,VCP,GRU,MAO,LIM,GUA
    RAW=data/instancias/XS python correr_heuristica.py --salida data/results/instancias/XS/heuristica_dt2 --dt 2
Todos los códigos leen los datos desde la variable de entorno RAW.
"""
import argparse
import os
import shutil

import numpy as np
import pandas as pd
import yaml

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'raw data')
DIAS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']


def generar(operadores, nombre, escala=None, salida=None, aeropuertos=None):
    rd = lambda f: pd.read_csv(os.path.join(RAW, f))
    reglas = yaml.safe_load(open(os.path.join(RAW, 'ops_rules.yaml'), encoding='utf-8'))
    fl, mt, tr = rd('fleet.csv'), rd('maintenance.csv'), rd('traffic_rights.csv')
    ap, lg, lf, cp = rd('airports.csv'), rd('legs_catalog.csv'), rd('landing_fees.csv'), rd('cost_params.csv')
    dw, dd, ia = rd('demand_weekly.csv'), rd('demand_daily.csv'), rd('interchange_airports.csv')
    faltan = set(operadores) - set(fl.operator)
    if faltan:
        raise ValueError(f'operadores desconocidos: {faltan}')

    fl2 = fl[fl.operator.isin(operadores)]
    mt2 = mt[mt.aircraft_id.isin(fl2.aircraft_id)]
    tr2 = tr[tr.operator.isin(operadores)]
    paises = set(tr2[tr2.allowed == 1].country)
    ap2 = ap[ap.country.isin(paises) | ap.iata.isin(mt2.airport)]
    if aeropuertos:   # red reducida: solo esos aeropuertos (más los de mantenimiento de la flota elegida)
        ap2 = ap2[ap2.iata.isin(aeropuertos) | ap2.iata.isin(mt2.airport)]
    aps = set(ap2.iata)
    lg2 = lg[lg.origin.isin(aps) & lg.dest.isin(aps)]
    pais = dict(zip(ap.iata, ap.country))
    ok = {(o, c) for o, c, a in zip(tr2.operator, tr2.country, tr2.allowed) if a == 1}
    maxb = float(reglas['max_continuous_block_hours'])
    volable = {(o, d) for o, d, b in zip(lg2.origin, lg2.dest, lg2.block_hours)
               if b <= maxb and any((op, pais[o]) in ok and (op, pais[d]) in ok for op in operadores)}

    # demanda: mercados dentro de la red, escalados a la misma relación demanda / capacidad que la instancia completa
    dentro = dw.origin.isin(aps) & dw.dest.isin(aps)
    frac_flota = fl2.payload_tons.sum() / fl.payload_tons.sum()
    if escala is None:
        escala = min(1.0, frac_flota * dw.tons_week.sum() / dw[dentro].tons_week.sum())
    dw2 = dw[dentro].copy()
    dd2 = dd[dd.origin.isin(aps) & dd.dest.isin(aps)].copy()
    dw2['tons_week'] = (dw2.tons_week * escala).round(1)
    for c in DIAS:
        dd2[c] = (dd2[c] * escala).round(1)
    # frecuencias: escaladas por la fracción de aviones, solo en tramos volables
    frac_aviones = len(fl2) / len(fl)
    f = np.floor(dw2.min_weekly_freq * frac_aviones + 0.5).astype(int)
    dw2['min_weekly_freq'] = np.where([(o, d) in volable for o, d in zip(dw2.origin, dw2.dest)], f, 0)

    salida = salida or os.path.join('data', 'instancias', nombre)
    os.makedirs(salida, exist_ok=True)
    for df, f_ in [(fl2, 'fleet.csv'), (mt2, 'maintenance.csv'), (tr2, 'traffic_rights.csv'), (ap2, 'airports.csv'),
                   (lg2, 'legs_catalog.csv'), (lf[lf.airport.isin(aps)], 'landing_fees.csv'),
                   (cp[cp.operator.isin(['ALL'] + list(operadores))], 'cost_params.csv'), (dw2, 'demand_weekly.csv'),
                   (dd2, 'demand_daily.csv'), (ia[ia.airport.isin(aps)], 'interchange_airports.csv')]:
        df.to_csv(os.path.join(salida, f_), index=False)
    shutil.copy(os.path.join(RAW, 'ops_rules.yaml'), os.path.join(salida, 'ops_rules.yaml'))
    resumen = {'operadores': list(operadores), 'aviones': len(fl2), 'aeropuertos': len(aps),
               'lista_aeropuertos': sorted(aps) if aeropuertos else 'todos los permitidos', 'tramos': len(lg2),
               'mercados': len(dw2), 'mercados_con_frecuencia': int((dw2.min_weekly_freq > 0).sum()),
               'frecuencias_semanales': int(dw2.min_weekly_freq.sum()), 'escala_demanda': round(float(escala), 4),
               'demanda_t_semana': round(float(dw2.tons_week.sum()), 1)}
    with open(os.path.join(salida, 'instancia.yaml'), 'w', encoding='utf-8') as fh:
        yaml.safe_dump(resumen, fh, allow_unicode=True, sort_keys=False)
    return salida, resumen


def main():
    ap = argparse.ArgumentParser(description='Instancia reducida con un subconjunto de operadores.')
    ap.add_argument('--operadores', required=True, help='lista separada por comas: AND, BRA, PAC, SUR')
    ap.add_argument('--nombre', required=True, help='nombre de la instancia (carpeta data/instancias/<nombre>)')
    ap.add_argument('--escala', type=float, default=None,
                    help='factor de la demanda; por defecto, el que conserva la relación demanda / capacidad')
    ap.add_argument('--aeropuertos', default=None,
                    help='red reducida: lista separada por comas (se agregan los aeropuertos de mantenimiento)')
    args = ap.parse_args()
    aps = [a.strip() for a in args.aeropuertos.split(',')] if args.aeropuertos else None
    salida, resumen = generar([o.strip() for o in args.operadores.split(',')], args.nombre, args.escala,
                              aeropuertos=aps)
    print(f'Instancia en {salida}:')
    for k, v in resumen.items():
        print(f'  {k}: {v}')


if __name__ == '__main__':
    main()
