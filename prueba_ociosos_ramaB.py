"""
Prueba de los aviones sin usar en la mejor solucion (rama B, 7.520.006 USD, sin usar AC-14 y AC-18).
Agrega una ida y vuelta operada por PAC a un avion ocioso, con el resto de los vuelos igual, recalcula filiales y
carga (warm_start_milp.resolver_carga_interchange) y valida con src/validador.py.

Uso, desde la raiz del repositorio (rama milp-completo-warm-start):
    py prueba_ociosos_ramaB.py scl    # AC-14: MIA-SCL-MIA (SCL-MIA es la ruta con mas demanda sin servir, 144 t)
    py prueba_ociosos_ramaB.py uio    # AC-18: MIA-UIO-MIA el jueves (8 t sin servir en UIO-MIA ese dia)
    py prueba_ociosos_ramaB.py base   # la solucion sin cambios, como control
Resultados del 12/11 (submodelo de carga con HiGHS): scl 7.420.106 (-99.900); uio 7.494.869 (-25.137).
"""
import sys, os, pickle, time, json
sys.path.insert(0, '.')
import warm_start_milp as ws, gurobi_model as gm
gm.INTERCHANGE = True
C = 'data/results/RamaB_interchange_continua'
it = pickle.load(open(os.path.join(C, 'modelo_resuelto.pkl'), 'rb'))['itinerario']
datos = gm.cargar_datos(); tau = datos['tau_e']
def ida_vuelta(a, b, salida, op='PAC'):
    t1 = round(salida + tau[(a, b)], 5); t2 = round(t1 + 70/60 + 1e-3, 5); t3 = round(t2 + tau[(b, a)], 5)
    return [dict(tramo=(a, b), t_dep=salida, t_arr=t1, op=op), dict(tramo=(b, a), t_dep=t2, t_arr=t3, op=op)]
caso = sys.argv[1]
planes = {
  'scl': {'AC-14': ida_vuelta('MIA', 'SCL', 30.0)},
  'uio': {'AC-18': ida_vuelta('MIA', 'UIO', 68.0)},
  'base': {},
}
it2 = dict(it)
for k, legs in planes[caso].items():
    for i, l in enumerate(legs, 1): l['pos'] = i
    it2[k] = legs
t0 = time.time()
multi, val = ws.resolver_carga_interchange(it2, 2.0, pasos=2)
k = val.get('kpis', {})
print(json.dumps({'caso': caso, 'ok': val['ok'], 'margen': round(val['margen'], 0), 'viol': val.get('violaciones', [])[:3],
                  'aviones': k.get('aviones_usados'), 'pct_demanda': k.get('pct_demanda'), 's': round(time.time() - t0)}))
