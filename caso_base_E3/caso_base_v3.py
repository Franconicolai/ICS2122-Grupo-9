"""
Módulo: caso_base_v3.py
Descripción: CASO BASE v3 = caso base v2 (regla miope, 25 frecuencias obligatorias) + CAMBIO DE OPERADOR (interchange).

Para qué existe (feedback de la Entrega 2 y decisión del profesor del 9 de noviembre):
  * El caso base v2 mantenía el operador de cada avión toda la semana, pero el modelo final permite que un avión sea
    operado por otra filial en los aeropuertos donde ambas están habilitadas (interchange_airports.csv). Comparar
    un modelo con cambio de operador contra un caso base sin él mezcla dos efectos. Esta versión pone al caso base
    bajo EXACTAMENTE las mismas reglas que la solución final: 25 frecuencias, demanda diaria estricta, mantenimiento,
    TAT 50/70/90, cierre cíclico, mínimo de 10 t por vuelo cargado, transbordos y cambio de operador.
  * Además, cada avión parte y cierra la semana en el mismo aeropuerto que en la solución final (ver PARTIDAS).

Qué se mantiene (es lo que lo hace un caso base): la política de programación de v2, COPIADA DE v2 (las funciones de
las secciones "datos", "rotaciones" y "programación" son las de caso_base_v2.py; los únicos cambios están marcados
con "NUEVO v3.1" y solo se activan si un avión parte fuera de su base de mantenimiento, ver PARTIDAS; con
CB_PARTIDAS=base, el comportamiento es idéntico al de v2 y los vuelos resultantes son los mismos, se verificó):
  decisiones secuenciales y miopes; siempre decide el avión que queda libre antes; evalúa rotaciones base -> ... -> base
  de 2 a 6 tramos con esperas de 0, 6, 12 y 18 h; puntúa (utilidad estimada + MU * frecuencias pendientes cubiertas)
  por hora; estima la carga con la demanda DIARIA que queda; rescata las frecuencias pendientes con recorridos de
  posicionamiento. Los vuelos se deciden suponiendo que cada avión usa su operador base (así se decidían en v2).

PARTIDAS (CB_PARTIDAS). Los datos no fijan en qué aeropuerto parte cada avión: la regla del problema es que la semana
cierre donde puede comenzar la siguiente. El caso base de v2 hacía partir y cerrar a cada avión en su base de
mantenimiento (MIA, salvo AC-06 en GRU); el modelo final elige libremente el aeropuerto de partida (en la solución
Rama B, 8 de 17 aviones parten en otro: BOG, MDE, LAX, GRU, UIO). Para comparar en igualdad de condiciones iniciales,
el caso base de v3 parte cada avión donde lo hace la solución final (por defecto, data/results/RamaB_interchange_continua;
CB_PARTIDAS acepta otra carpeta o un itinerario_vuelos.csv). Los aviones que esa solución no usa (AC-14, AC-18) parten
de su base. CB_PARTIDAS=base devuelve cada avión a su base: sirve para comprobar que la política es la de v2 y como
sensibilidad, pero NO es el caso base que se reporta.
Cuando un avión parte fuera de su base, la política debe además pasar por su aeropuerto de mantenimiento: llegar antes
de que empiece la ventana y no salir hasta que termine (regla del validador). Para eso solo se agrega esto: (a) una
rotación vale si termina con tiempo para que el avión todavía llegue a su base antes de la ventana, o si es una rotación
que pasa por la base durante la ventana; (b) si ya no cabe otra rotación y falta el mantenimiento, se programa la
rotación de mantenimiento de mayor puntaje aunque sea negativo.
ATENCIÓN: las partidas de la solución final son decisiones del modelo, no datos del problema. Usarlas es una
comparación controlada (mismas condiciones iniciales, distinto método de decisión), no un caso base independiente de la
solución; hay que declararlo al presentarlo.

Qué se agrega (etapas 3 a 5; las etapas 1 y 2 son la política de v2):
  1. Programación miope de vuelos (v2, sin cambios).                        -> itinerario con el operador base
  2. Asignación óptima de la carga sobre esos vuelos (v2, sin cambios).     -> "caso base con operador fijo"
  3. Asignación de la filial que opera cada vuelo, con los vuelos y la carga fijos: programación dinámica exacta por
     avión (src/asignar_operadores.py). Cada vuelo paga los derechos, el ex-fuel por hora y el handling de la filial
     que lo opera; solo se cambia de filial en aeropuertos donde ambas están habilitadas.
  4. Reasignación de la carga con las filiales ya fijadas (el handling y el soporte de transbordos dependen de la filial).
  5. Se alternan 3 y 4 mientras el margen validado suba (máximo CB_PASOS veces). Ningún paso empeora el anterior.
  El cambio de operador NO modifica los vuelos ni los horarios: es un beneficio que el caso base obtiene "después",
  sin anticiparlo al programar. Por eso el caso base sigue siendo miope: el modelo final sí lo decide junto con los vuelos.

Supuestos del cambio de operador (los mismos que el modelo final; docs/README_interchanges.md):
  - no tiene costo ni tiempo en tierra adicional (el TAT no cambia);
  - entre dos vuelos seguidos solo se cambia de filial en un aeropuerto donde ambas filiales están habilitadas;
  - cada vuelo usa los derechos de tráfico, el ex-fuel y el handling de la filial que lo opera;
  - borde de la semana: por defecto ('flexible', decisión del grupo del 10-11) basta que el avión vuelva a su operador
    base en tierra, en el aeropuerto donde parte y termina la semana, si allí están habilitadas ambas filiales.
    Con CB_BORDE=base, el primer y el último vuelo los opera el operador base (lectura estricta);
  - el mantenimiento no depende de la filial.

Qué se mide: el validador independiente (src/validador.py) recalcula el margen y comprueba todas las reglas; es el único
número que se reporta. El script entrega las dos versiones de una misma corrida:
  - caso base con operador fijo (etapa 2): equivale a v2;
  - caso base con cambio de operador (etapa 5): el que se compara con la solución final.

Variables de entorno (todas opcionales):
    SOLVER            'gurobi' (por defecto aquí) o 'highs' (gratuito, más lento)
    THREADS           hilos del solver
    TTR_H             tiempo mínimo de transbordo en horas (2 por defecto, igual que el modelo)
    TLIM_CARGA        segundos para cada asignación de carga (600 por defecto)
    GAP_CARGA         brecha de la asignación de carga (1e-6 por defecto, como en el resto del repositorio)
    CB_CAMBIO_OPERADOR  1 (por defecto) agrega el cambio de operador; 0 entrega solo el caso base con operador fijo
    CB_BORDE          'flexible' (por defecto) o 'base' (ver arriba)
    CB_PASOS          máximo de alternancias filiales <-> carga (5 por defecto)
    CB_PARTIDAS       carpeta o CSV de la solución cuyas partidas se copian (por defecto data/results/RamaB_interchange_continua);
                      'base' = cada avión parte de su base de mantenimiento (ver arriba)
    CB_FRECUENCIAS    1 (por defecto) exige las 25 frecuencias; 0 las ignora (solo para sensibilidad)
    CB_MAX_TRAMOS     largo máximo de una rotación (6 por defecto)
    CB_MU             bono (USD) por frecuencia pendiente cubierta en la puntuación de una rotación (20.000 por defecto).
                      Si con ese bono quedan frecuencias sin cubrir, main() prueba 30.000, 50.000, 100.000, 200.000 y
                      500.000 y se queda con el primero que las cubre todas (con las partidas de Rama B hace falta llegar a 50.000;
                      con partidas = base, 20.000 alcanza)
    CB_ESPERAS        esperas candidatas antes de salir, en horas, separadas por coma (0,6,12,18 por defecto)
    SALIDA            prefijo de los archivos de salida (por defecto data/results/caso_base_v3/caso_base_v3, o
                      .../caso_base_v3_base si CB_PARTIDAS=base)

Uso (desde la raíz del repositorio, con data/raw data/ en su lugar):
    SOLVER=gurobi python src/caso_base_v3.py                      # caso base (partidas de la solución final)
    CB_PARTIDAS=base SOLVER=gurobi python src/caso_base_v3.py     # cada avión parte de su base (verificación vs v2)
    CB_BORDE=base SOLVER=gurobi python src/caso_base_v3.py        # lectura estricta del borde
    CB_CAMBIO_OPERADOR=0 SOLVER=gurobi python src/caso_base_v3.py # solo operador fijo (reproduce v2)

Archivos de salida (con el prefijo SALIDA):
    _itinerario.csv         vuelos finales; la columna 'filial' es la que opera cada vuelo
    _kpis.json / _kpis.csv  KPIs del caso base final (con cambio de operador) y del de operador fijo
    _kpis_operador_fijo.json
    _vuelos_detalle.csv     carga a bordo y costo por vuelo (con la filial que lo opera)
    _interchanges.csv       cada cambio de filial: avión, aeropuerto, filial anterior y nueva
    _historial.csv          margen validado en cada paso de la alternancia
    _partidas.csv           aeropuerto de partida de cada avión frente a su base de mantenimiento
"""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd
import yaml

os.environ.setdefault('SOLVER', 'gurobi')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asignacion_carga_multitramos as acm  # noqa: E402
from asignar_operadores import asignar_operadores, cargar_datos_operadores  # noqa: E402
from validador import validar  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = acm.RAW
H = 168.0
EPS_T = 1e-6
UMBRAL_LLENO = 0.95   # un vuelo está "lleno" con >= 95 % del payload (mismo supuesto que el modelo)
FRECUENCIAS = os.environ.get('CB_FRECUENCIAS', '1') == '1'
CAMBIO_OPERADOR = os.environ.get('CB_CAMBIO_OPERADOR', '1') == '1'
BORDE_BASE = os.environ.get('CB_BORDE', 'flexible').lower() == 'base'
PARTIDAS = os.environ.get('CB_PARTIDAS', os.path.join(RAIZ, 'data', 'results', 'RamaB_interchange_continua'))
PASOS = int(os.environ.get('CB_PASOS', 5))
TLIM_CARGA = float(os.environ.get('TLIM_CARGA', 600))
MAX_TRAMOS = int(os.environ.get('CB_MAX_TRAMOS', 6))
MU = float(os.environ.get('CB_MU', 20000.0))   # bono (USD) por frecuencia pendiente cubierta
ESPERAS = [float(x) for x in os.environ.get('CB_ESPERAS', '0,6,12,18').split(',')]
SALIDA = os.environ.get('SALIDA', os.path.join(RAIZ, 'data', 'results', 'caso_base_v3',
                                               'caso_base_v3' + ('_base' if PARTIDAS.lower() == 'base' else '')))

# ==========================================================================================
# ETAPAS 1 y 2: política de v2 (de aquí hasta la sección "KPIs"); solo cambian los puntos marcados "NUEVO v3.1"
# ==========================================================================================

# ------------------------------------------------------------------------------------------ datos
def cargar_datos(ruta_raw=RAW):
    """Lee los CSV y las reglas operativas y arma lo que necesita el caso base."""
    rd = lambda f: pd.read_csv(os.path.join(ruta_raw, f))
    ap, fl, lg = rd('airports.csv'), rd('fleet.csv'), rd('legs_catalog.csv')
    dw, dd, tr = rd('demand_weekly.csv'), rd('demand_daily.csv'), rd('traffic_rights.csv')
    cp, lf, mt = rd('cost_params.csv'), rd('landing_fees.csv'), rd('maintenance.csv')
    with open(os.path.join(ruta_raw, 'ops_rules.yaml'), encoding='utf-8') as f:
        reglas = yaml.safe_load(f)

    pais = dict(zip(ap.iata, ap.country))
    operador = dict(zip(fl.aircraft_id, fl.operator))
    payload = dict(zip(fl.aircraft_id, fl.payload_tons.astype(float)))
    permitido = {(o, c) for o, c, a in zip(tr.operator, tr.country, tr.allowed) if a == 1}
    tec = set(reglas['technical_stop_airports'])
    tat = {k: v / 60.0 for k, v in reglas['tat_minutes'].items()}
    max_bloque = float(reglas['max_continuous_block_hours'])
    min_ton = float(reglas['min_tons_per_extra_stop'])
    c = {(p, o): v for p, o, v in zip(cp.param, cp.operator, cp.value)}
    combustible_h = c[('fuel_burn_gal_per_hour', 'ALL')] * c[('fuel_price_usd_per_gal', 'ALL')]
    tasa = dict(zip(lf.airport, lf.fee_usd))
    tau = {(o, d): float(b) for o, d, b in zip(lg.origin, lg.dest, lg.block_hours)}
    tarifa = {(o, d): float(r) for o, d, r in zip(dw.origin, dw.dest, dw.tariff_usd_per_kg)}
    demanda = {(o, d): float(t) for o, d, t in zip(dw.origin, dw.dest, dw.tons_week)}
    freq = {(o, d): int(f) for o, d, f in zip(dw.origin, dw.dest, dw.min_weekly_freq) if f > 0}
    dem_dia = {(r.origin, r.dest, i): float(getattr(r, col))
               for r in dd.itertuples()
               for i, col in enumerate(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], start=1)
               if float(getattr(r, col)) > 1e-9}

    # tramos que puede volar cada avión: derechos de tráfico en origen y destino y límite de 9 h
    Ek = {k: {e for e in tau if tau[e] <= max_bloque
              and (operador[k], pais[e[0]]) in permitido and (operador[k], pais[e[1]]) in permitido}
          for k in fl.aircraft_id}
    costo = {(k, e): (combustible_h + c[('ex_fuel_usd_per_block_hour', operador[k])]) * tau[e] + tasa[e[1]]
             for k in fl.aircraft_id for e in Ek[k]}
    handling = {k: c[('handling_usd_per_ton', operador[k])] for k in fl.aircraft_id}

    # mantenimiento: (aeropuerto base, inicio, fin) en horas desde el inicio de la semana
    ini = datetime.fromisoformat(str(reglas['week_start']))
    mant = {}
    for r in mt.itertuples():
        h0 = (datetime.fromisoformat(r.start_datetime) - ini).total_seconds() / 3600.0
        h1 = (datetime.fromisoformat(r.end_datetime) - ini).total_seconds() / 3600.0
        mant[r.aircraft_id] = (r.airport, h0, h1)

    return dict(K=list(fl.aircraft_id), A=sorted(pais), operador=operador, payload=payload, tec=tec, tat=tat,
                min_ton=min_ton, tau=tau, tarifa=tarifa, demanda=demanda, dem_dia=dem_dia, freq=freq, Ek=Ek,
                costo=costo, handling=handling, mant=mant)


# ------------------------------------------------------------------------------------------ rotaciones
def _rotaciones(av, d, max_tramos):
    """Todos los ciclos base -> ... -> base de 2 a max_tramos tramos (sin repetir aeropuertos) con los tramos que el
    avión puede volar y al menos un tramo con demanda. Se calculan una vez por (operador, base)."""
    clave = (d['operador'][av['k']], av['base'], max_tramos)
    cache = d.setdefault('_rotaciones', {})
    if clave in cache:
        return cache[clave]
    b = av['base']
    sal = defaultdict(list)
    for (o, x) in d['Ek'][av['k']]:
        sal[o].append(x)
    for o in sal:
        sal[o].sort()
    out = []

    def dfs(actual, camino, visitados):
        for x in sal[actual]:
            e = (actual, x)
            if x == b:
                if camino:
                    tour = tuple(camino + [e])
                    if any(t in d['tarifa'] and t[0] not in d['tec'] and t[1] not in d['tec'] for t in tour):
                        out.append(tour)
                continue
            if x in visitados or len(camino) + 2 > max_tramos:
                continue
            camino.append(e)
            visitados.add(x)
            dfs(x, camino, visitados)
            camino.pop()
            visitados.discard(x)

    dfs(b, [], {b})
    cache[clave] = out
    return out


def _ruta(av, a, b, d):
    """Camino más corto (en horas, con tiempos en tierra) de a a b con los tramos que el avión puede volar.
    El catálogo es asimétrico (p. ej. no existe FRA->MIA). Devuelve la lista de tramos ([] si a == b) o None."""
    if a == b:
        return []
    clave = (d['operador'][av['k']], a, b)
    cache = d.setdefault('_rutas', {})
    if clave in cache:
        return cache[clave]
    tat = d['tat']
    dist, previo, pend_ = {a: 0.0}, {}, {a}
    hecho = set()
    while pend_:
        u = min(pend_, key=lambda x: (dist[x], x))
        pend_.discard(u)
        hecho.add(u)
        if u == b:
            break
        for e in sorted(d['Ek'][av['k']]):
            if e[0] != u or e[1] in hecho:
                continue
            w = dist[u] + d['tau'][e] + (tat['technical_stop'] if e[1] in d['tec'] else tat['default'])
            if w < dist.get(e[1], float('inf')) - 1e-12:
                dist[e[1]], previo[e[1]] = w, e
                pend_.add(e[1])
    if b not in previo:
        cache[clave] = None
        return None
    camino, x = [], b
    while x != a:
        camino.append(previo[x])
        x = previo[x][0]
    cache[clave] = camino[::-1]
    return cache[clave]


# ------------------------------------------------------------------------------------------ programación
def _carga_dia(av, e, t_dep, d, remd):
    """Carga que se espera llevar en el tramo e si sale en t_dep: demanda DIARIA que queda ese día (la carga solo
    se embarca el día UTC de su demanda). Cero si no hay demanda, si es escala técnica o si no llega al mínimo."""
    if e not in d['tarifa'] or e[0] in d['tec'] or e[1] in d['tec']:
        return 0.0
    dia = int(t_dep // 24) + 1
    c = min(av['Q'], remd.get((e[0], e[1], dia), 0.0))
    return c if c >= d['min_ton'] else 0.0


# ------------------------------------------------------------------------------------------ NUEVO v3.1
# Partidas distintas de la base de mantenimiento (solo con CB_PARTIDAS). Si el avión parte en el aeropuerto de su
# ventana de mantenimiento (caso de v2) nada de esto se activa y el comportamiento es idéntico al de v2.
def _fuera_de_base(av):
    """True si el avión parte (y cierra su semana) en un aeropuerto distinto del de su ventana de mantenimiento."""
    return av['base'] != av['mant_ap']


def _en_mantenimiento_fuera_de_base(av, e, arr):
    """El tramo e llega al aeropuerto de mantenimiento antes de que empiece la ventana: el avión debe quedarse ahí."""
    return (_fuera_de_base(av) and not av['mant_hecha'] and e[1] == av['mant_ap'] and arr <= av['ini'] - EPS_T)


def _es_rotacion_de_mantenimiento(av, tramos, prog):
    """La rotación pasa por el aeropuerto de mantenimiento, llega antes de que empiece la ventana y no sale hasta que
    termina (la misma condición que exige el validador)."""
    for i in range(len(tramos) - 1):
        if tramos[i][1] == av['mant_ap']:
            return prog[i][1] <= av['ini'] - EPS_T + 1e-9 and prog[i + 1][0] >= av['fin'] - 1e-9
    return False


def _tiempo_hasta_mantenimiento(av, d):
    """Horas mínimas (con tiempos en tierra) para ir del aeropuerto de partida al de mantenimiento."""
    clave = (d['operador'][av['k']], av['base'], av['mant_ap'])
    cache = d.setdefault('_t_mant', {})
    if clave not in cache:
        ruta = _ruta(av, av['base'], av['mant_ap'], d)
        if ruta is None:
            cache[clave] = float('inf')
        else:
            t = 0.0
            for j, e in enumerate(ruta):
                t += d['tau'][e]
                if j + 1 < len(ruta):
                    t += d['tat']['technical_stop'] if e[1] in d['tec'] else d['tat']['default']
            cache[clave] = t
    return cache[clave]


def _cumple_mantenimiento_fuera_de_base(av, tramos, prog, d):
    """Una rotación de un avión que parte fuera de su base de mantenimiento (y aún no lo hizo) es válida si (a) es una
    rotación de mantenimiento, o (b) termina con tiempo suficiente para que el avión todavía llegue a su base de
    mantenimiento antes de que empiece la ventana. Así la política nunca se queda sin poder hacer el mantenimiento."""
    if av['mant_hecha'] or _es_rotacion_de_mantenimiento(av, tramos, prog):
        return True
    t_libre = prog[-1][1] + d['tat']['full_in_full_out'] + EPS_T
    return t_libre + _tiempo_hasta_mantenimiento(av, d) <= av['ini'] - EPS_T


def _forzar_mantenimiento(av, d, remd, pend, mu, max_tramos, esperas):
    """Si ya no cabe ninguna rotación normal y al avión le falta el mantenimiento, programa la rotación de
    mantenimiento de mayor puntaje, aunque sea negativo (sin mantenimiento la solución sería infactible)."""
    ap, ancla = av['mant_ap'], av['base']
    tours = [t for t in _rotaciones(av, d, max_tramos) if any(e[1] == ap for e in t[:-1])]
    ida, vuelta = (ancla, ap), (ap, ancla)
    if ida in d['Ek'][av['k']] and vuelta in d['Ek'][av['k']] and (ida, vuelta) not in tours:
        tours.append((ida, vuelta))
    mejor = None
    for tour in tours:
        cubre = len({e for e in tour if pend.get(e, 0) > 0})
        for w in esperas:
            ev = _estimar(av, tour, av['t'] + w, d, remd)
            if ev is None:
                continue
            prog, cargas, util = ev
            puntaje = (util + mu * cubre) / (prog[-1][1] - av['t'] + d['tat']['full_in_full_out'])
            if mejor is None or puntaje > mejor[0] + 1e-9:
                mejor = (puntaje, tour, cargas, prog)
    if mejor is None:
        return False
    _agregar(av, mejor[1], mejor[2], mejor[3], remd, pend, d)
    return True


def _programar(av, tramos, cargas, d, t0):
    """Horas de salida y llegada de una secuencia de tramos que parte en t0, con el TAT que corresponde según las
    cargas estimadas (50 min escala técnica, 90 lleno-lleno, 70 resto). Devuelve None si no cierra la semana (con el
    TAT de cierre, que se toma conservadoramente de 90 min) o si pisaría la ventana de mantenimiento."""
    tat = d['tat']
    dep = t0
    prog = []
    for i, e in enumerate(tramos):
        arr = dep + d['tau'][e]
        prog.append((dep, arr))
        if i + 1 < len(tramos):
            if e[1] in d['tec']:
                espera = tat['technical_stop']
            elif cargas[i] >= UMBRAL_LLENO * av['Q'] and cargas[i + 1] >= UMBRAL_LLENO * av['Q']:
                espera = tat['full_in_full_out']
            else:
                espera = tat['default']
            dep = arr + espera + EPS_T
            if _en_mantenimiento_fuera_de_base(av, e, arr):      # NUEVO v3.1 (solo si parte fuera de su base)
                dep = max(dep, av['fin'] + EPS_T)
    fin_viaje = prog[-1][1]
    if fin_viaje + tat['full_in_full_out'] > H - EPS_T:
        return None
    if not _fuera_de_base(av):                                  # v2, sin cambios
        if not av['mant_hecha'] and prog[0][0] < av['fin'] and fin_viaje > av['ini']:
            return None
    elif not _cumple_mantenimiento_fuera_de_base(av, tramos, prog, d):   # NUEVO v3.1
        return None
    return prog


def _estimar(av, tramos, t0, d, remd):
    """Programa el recorrido y estima su carga por día de salida. Como el día depende de las horas y las horas del
    TAT (que depende de la carga), se itera dos veces. Devuelve (prog, cargas, utilidad) o None si no cabe."""
    tat = d['tat']
    dep = t0
    fechas = []
    for e in tramos:
        fechas.append(dep)
        dep += d['tau'][e] + (tat['technical_stop'] if e[1] in d['tec'] else tat['default']) + EPS_T
    cargas = [_carga_dia(av, e, td, d, remd) for e, td in zip(tramos, fechas)]
    prog = _programar(av, tramos, cargas, d, t0)
    if prog is None:
        return None
    cargas = [_carga_dia(av, e, p[0], d, remd) for e, p in zip(tramos, prog)]
    prog = _programar(av, tramos, cargas, d, t0)
    if prog is None:
        return None
    ing = 1000.0 * sum(d['tarifa'].get(e, 0.0) * c for e, c in zip(tramos, cargas))
    costo = sum(d['costo'][(av['k'], e)] for e in tramos) + d['handling'][av['k']] * sum(cargas)
    return prog, cargas, ing - costo


def _agregar(av, tramos, cargas, prog, remd, pend, d):
    """Fija el recorrido en el itinerario del avión, descuenta la demanda diaria estimada y las frecuencias."""
    for e, (td, ta) in zip(tramos, prog):
        av['legs'].append({'pos': len(av['legs']) + 1, 'tramo': e, 't_dep': td, 't_arr': ta})
    for e, c, (td, _) in zip(tramos, cargas, prog):
        if c > 0:
            q = (e[0], e[1], int(td // 24) + 1)
            remd[q] = remd.get(q, 0.0) - c
        if pend.get(e, 0) > 0:
            pend[e] -= 1
    av['t'] = prog[-1][1] + d['tat']['full_in_full_out'] + EPS_T     # colchón de 90 min en la base
    if _fuera_de_base(av):                                      # NUEVO v3.1
        if _es_rotacion_de_mantenimiento(av, tramos, prog):
            av['mant_hecha'] = True
    elif av['t'] > av['fin']:                                   # v2, sin cambios
        av['mant_hecha'] = True


def _fase_principal(avs, remd, pend, d, mu, max_tramos, esperas):
    """Avanza en el tiempo: siempre decide el avión que queda libre antes. Para ese avión evalúa todas las rotaciones
    desde su base (y varias esperas antes de salir) y elige la de mayor (utilidad + MU * frecuencias pendientes
    cubiertas) por hora. Si ninguna tiene puntuación positiva, el avión termina su semana."""
    activos = set(avs)
    tat_cierre = d['tat']['full_in_full_out']
    while activos:
        k = min(activos, key=lambda k: (avs[k]['t'], -avs[k]['Q'], k))
        av = avs[k]
        mejor = None
        for tour in _rotaciones(av, d, max_tramos):
            cubre = len({e for e in tour if pend.get(e, 0) > 0})
            for w in esperas:
                t0 = av['t'] + w
                ev = _estimar(av, tour, t0, d, remd)
                if ev is None:
                    continue
                prog, cargas, util = ev
                puntaje = (util + mu * cubre) / (prog[-1][1] - av['t'] + tat_cierre)
                if puntaje > 0 and (mejor is None or puntaje > mejor[0] + 1e-9):
                    mejor = (puntaje, tour, cargas, prog)
        if mejor is None:
            if _fuera_de_base(av) and not av['mant_hecha']:        # NUEVO v3.1
                # un avión sin vuelos no necesita mantenimiento (queda ocioso); si ya opera, se fuerza la rotación
                if av['legs'] and _forzar_mantenimiento(av, d, remd, pend, mu, max_tramos, esperas):
                    continue
                if av['legs']:
                    print(f"ATENCIÓN: {k} no pudo programar su mantenimiento en {av['mant_ap']}")
                activos.discard(k)
                continue
            # nada cabe antes de la ventana de mantenimiento: esperar a que termine y reintentar
            if not av['mant_hecha'] and av['t'] < av['fin']:
                av['t'], av['mant_hecha'] = av['fin'] + EPS_T, True
                continue
            activos.discard(k)
            continue
        _, tour, cargas, prog = mejor
        _agregar(av, tour, cargas, prog, remd, pend, d)


def _fase_rescate(avs, remd, pend, d):
    """Último recurso: si quedan frecuencias sin cubrir, programa recorridos de posicionamiento base ~> o, (o,d),
    d ~> base por el camino más corto (como en el caso base anterior), eligiendo el que cubre más por hora."""
    while any(v > 0 for v in pend.values()):
        mejor = None
        for av in sorted(avs.values(), key=lambda a: a['t']):
            Ek = d['Ek'][av['k']]
            for (o, dd_), f in pend.items():
                if f <= 0 or o in d['tec'] or dd_ in d['tec'] or (o, dd_) not in Ek:
                    continue
                ida, vuelta = _ruta(av, av['base'], o, d), _ruta(av, dd_, av['base'], d)
                if ida is None or vuelta is None:
                    continue
                tramos = ida + [(o, dd_)] + vuelta
                tmp, cubre = dict(pend), 0
                for e in tramos:
                    if tmp.get(e, 0) > 0:
                        tmp[e] -= 1
                        cubre += 1
                ev = _estimar(av, tramos, av['t'], d, remd)
                if ev is None or cubre == 0:
                    continue
                prog, cargas, _ = ev
                valor = cubre / (prog[-1][1] - av['t'] + d['tat']['full_in_full_out'])
                if mejor is None or valor > mejor[0] + 1e-12:
                    mejor = (valor, av, tramos, cargas, prog)
        if mejor is None:
            break
        _, av, tramos, cargas, prog = mejor
        _agregar(av, tramos, cargas, prog, remd, pend, d)
    return {e: f for e, f in pend.items() if f > 0}


def _nuevos_aviones(d):
    avs = {}
    for k in d['K']:
        mant_ap, ini, fin = d['mant'][k]
        base = d.get('partidas', {}).get(k, mant_ap)   # NUEVO v3.1: aeropuerto de partida y cierre (por defecto, la base)
        avs[k] = dict(k=k, base=base, mant_ap=mant_ap, ini=ini, fin=fin, Q=d['payload'][k], legs=[], t=0.0,
                      mant_hecha=False)
    return avs


def construir_caso_base(d, con_frecuencias=True, max_tramos=MAX_TRAMOS, mu=MU, esperas=None):
    """Devuelve ({avion: [ {pos, tramo, t_dep, t_arr}, ... ]}, frecuencias que no se pudieron cubrir).
    Una pasada con bono MU bajo (así las frecuencias casi no distorsionan la política de utilidad por hora) y, si
    quedan frecuencias pendientes, recorridos de posicionamiento de último recurso."""
    esperas = esperas or ESPERAS
    avs = _nuevos_aviones(d)
    remd = dict(d['dem_dia'])
    pend = dict(d['freq']) if con_frecuencias else {}
    _fase_principal(avs, remd, pend, d, mu if con_frecuencias else 0.0, max_tramos, esperas)
    sin_cubrir = {e: f for e, f in pend.items() if f > 0}
    if sin_cubrir:
        sin_cubrir = _fase_rescate(avs, remd, pend, d)
    itinerario = {k: av['legs'] for k, av in avs.items() if av['legs']}
    return itinerario, sin_cubrir


# ------------------------------------------------------------------------------------------ KPIs
# ------------------------------------------------------------------------------------------ KPIs
def calcular_kpis(d, itinerario, v, res):
    demanda_total = sum(d['demanda'].values())
    ent = v['toneladas_entregadas']
    capacidad = sum(d['payload'][k] * len(ls) for k, ls in itinerario.items())
    a_bordo = sum(c['x'] for c in res['solucion']['cargas'])
    horas = sum(l['t_arr'] - l['t_dep'] for ls in itinerario.values() for l in ls)
    vuelos_od = {}
    for ls in itinerario.values():
        for l in ls:
            vuelos_od[l['tramo']] = vuelos_od.get(l['tramo'], 0) + 1
    pct = lambda a, b: round(100 * a / b, 1) if b else 0.0
    return {
        'validador_ok': v['ok'], 'violaciones': v['violaciones'][:30],
        'margen_usd': round(v['margen']), 'ingresos_usd': round(v['ingresos']),
        'costo_vuelos_usd': round(v['costo_vuelos']), 'handling_usd': round(v['handling']),
        'toneladas_entregadas': round(ent, 1), 'pct_demanda_servida': pct(ent, demanda_total),
        'vuelos': v['vuelos'], 'aviones_usados': len(itinerario), 'aviones_flota': len(d['K']),
        'horas_bloque': round(horas, 1),
        # factor de ocupación = toneladas a bordo (suma sobre vuelos) / capacidad ofrecida (payload x vuelos)
        'factor_ocupacion_pct': pct(a_bordo, capacidad),
        'ocupacion_entregada_pct': pct(ent, capacidad),
        'utilizacion_flota_pct': pct(horas, len(d['K']) * H),
        'utilizacion_usados_pct': pct(horas, len(itinerario) * H),
        'costo_por_tonelada_usd': round((v['costo_vuelos'] + v['handling']) / ent) if ent > 0 else None,
        'frecuencias_cumplidas': f"{sum(1 for e, f in d['freq'].items() if vuelos_od.get(e, 0) >= f)}/{len(d['freq'])}",
        'estado_solver_carga': res['estado'], 'gap_carga': res['gap'], 'ttr_h': acm.TTR_H,
        **res['estadisticas'],
    }


# ==========================================================================================
# ETAPAS 3 a 5: cambio de operador (NUEVO en v3)
# ==========================================================================================
def leer_partidas(ruta, d):
    """Aeropuerto de partida de cada avión. 'base' -> {} (cada avión parte de su base de mantenimiento). Si es una
    carpeta o un itinerario_vuelos.csv (columnas avion, pos, origen), se toma el origen del primer vuelo de cada avión;
    los aviones que esa solución no usa parten de su base."""
    if ruta.lower() == 'base':
        return {}
    archivo = os.path.join(ruta, 'itinerario_vuelos.csv') if os.path.isdir(ruta) else ruta
    if not os.path.isfile(archivo):
        raise SystemExit(f'CB_PARTIDAS: no existe {archivo}. Indique la carpeta o el itinerario_vuelos.csv de la solución '
                         f'final (está en la rama milp-completo-warm-start), o CB_PARTIDAS=base.')
    df = pd.read_csv(archivo).sort_values(['avion', 'pos'])
    partidas = dict(df.groupby('avion').first()['origen'])
    for k, a in partidas.items():
        if k not in d['K']:
            raise SystemExit(f'CB_PARTIDAS: avión desconocido {k}')
        if a not in d['A']:
            raise SystemExit(f'CB_PARTIDAS: aeropuerto desconocido {a} para {k}')
    return partidas


def cargar_datos_v3():
    """Datos de v2 más lo que hace falta para costear un vuelo con la filial que lo opera."""
    d = cargar_datos()
    d['partidas'] = leer_partidas(PARTIDAS, d)
    cp = pd.read_csv(os.path.join(RAW, 'cost_params.csv'))
    lf = pd.read_csv(os.path.join(RAW, 'landing_fees.csv'))
    c = {(p, o): v for p, o, v in zip(cp.param, cp.operator, cp.value)}
    d['combustible_h'] = c[('fuel_burn_gal_per_hour', 'ALL')] * c[('fuel_price_usd_per_gal', 'ALL')]
    d['exf'] = {o: v for (p, o), v in c.items() if p == 'ex_fuel_usd_per_block_hour'}
    d['tasa'] = dict(zip(lf.airport, lf.fee_usd))
    return d


def _config_validador():
    """Mismas reglas que el modelo final; 'borde_operador_base' según CB_BORDE."""
    return {'ciclico': True, 'tat_variable': True, 'min_ton_escala': True, 'mantenimiento': True,
            'frecuencias': FRECUENCIAS, 'demanda_diaria': True, 'dist_frecuencias': False,
            'transferencias': True, 'ttr_h': acm.TTR_H, 'interchanges': True, 'borde_operador_base': BORDE_BASE}


def resolver_carga_y_validar(itinerario):
    """Asigna la carga de forma óptima sobre los vuelos (y filiales) dados y valida la solución completa."""
    res = acm.asignar_carga_multitramos(itinerario, ruta_raw=RAW, ttr_h=acm.TTR_H, tlim=TLIM_CARGA, gap=acm.GAP_CARGA)
    if not res['ok']:
        raise SystemExit(f"La asignación de carga no encontró solución: {res['estado']}")
    v = validar(res['solucion'], _config_validador(), ruta_raw=RAW, freq_exigibles=None if FRECUENCIAS else {})
    return res, v


def asignar_cambio_operador(itinerario, res0, v0):
    """Etapas 3 a 5. Parte del caso base con operador fijo (itinerario, res0, v0) y alterna:
         filiales (programación dinámica exacta con la carga fija)  ->  carga (con las filiales fijas)
    mientras el margen validado suba. Devuelve (mejor, historial); 'mejor' es el último paso aceptado."""
    D = cargar_datos_operadores(RAW)
    n0 = v0['kpis']['interchanges']
    mejor = {'itinerario': itinerario, 'res': res0, 'v': v0}
    hist = [{'paso': 0, 'descripcion': 'operador fijo (etapa 2)', 'margen_usd': round(v0['margen']),
             'interchanges': n0, 'validador_ok': v0['ok'], 'aceptado': True}]
    actual = res0
    for paso in range(1, PASOS + 1):
        try:
            it_op, resumen = asignar_operadores(actual['solucion'], D, borde_base=BORDE_BASE)
        except ValueError:
            # la carga actual impide toda asignación de filiales (un transbordo en un aeropuerto sin las filiales
            # que necesita el tramo): se asignan filiales sin carga y la carga se vuelve a resolver después
            try:
                it_op, resumen = asignar_operadores(dict(actual['solucion'], cargas=[], transferencias=[]), D,
                                                    borde_base=BORDE_BASE)
            except ValueError:
                print(f'  paso {paso}: no hay asignación de filiales factible; se detiene.', flush=True)
                break
        res, v = resolver_carga_y_validar(it_op)
        acepta = v['ok'] and v['margen'] > mejor['v']['margen'] + 1
        hist.append({'paso': paso, 'descripcion': 'filiales (PD exacta) + carga reasignada',
                     'margen_usd': round(v['margen']), 'interchanges': v['kpis']['interchanges'],
                     'validador_ok': v['ok'], 'aceptado': acepta})
        print(f"  paso {paso}: margen {v['margen']:,.0f} USD | {v['kpis']['interchanges']} interchanges | "
              f"validador {'OK' if v['ok'] else v['violaciones'][:2]} | {'aceptado' if acepta else 'sin mejora: se detiene'}",
              flush=True)
        if not acepta:
            break
        mejor, actual = {'itinerario': it_op, 'res': res, 'v': v}, res
    return mejor, hist


def filial_de(leg, base):
    """Filial que opera un vuelo: la del campo 'op' si existe; si no, el operador base del avión."""
    return leg.get('op') or base


def lista_interchanges(itinerario, d):
    """Cada cambio de filial. Los 'internos' ocurren entre dos vuelos seguidos; los de 'borde' (solo con borde
    flexible) son el cambio al operador base en tierra, en el aeropuerto donde parte y termina la semana."""
    filas = []
    for k, legs in itinerario.items():
        legs = sorted(legs, key=lambda l: l['pos'])
        base = d['operador'][k]
        ops = [filial_de(l, base) for l in legs]
        for i in range(len(legs) - 1):
            if ops[i] != ops[i + 1]:
                filas.append({'aeronave': k, 'tipo': 'interno', 'aeropuerto': legs[i]['tramo'][1],
                              'despues_del_vuelo': legs[i]['pos'], 'filial_anterior': ops[i],
                              'filial_nueva': ops[i + 1]})
        if not BORDE_BASE:
            ap = legs[0]['tramo'][0]
            if ops[0] != base:
                filas.append({'aeronave': k, 'tipo': 'borde (inicio de semana)', 'aeropuerto': ap,
                              'despues_del_vuelo': 0, 'filial_anterior': base, 'filial_nueva': ops[0]})
            if ops[-1] != base:
                filas.append({'aeronave': k, 'tipo': 'borde (fin de semana)', 'aeropuerto': ap,
                              'despues_del_vuelo': legs[-1]['pos'], 'filial_anterior': ops[-1], 'filial_nueva': base})
    return pd.DataFrame(filas, columns=['aeronave', 'tipo', 'aeropuerto', 'despues_del_vuelo', 'filial_anterior',
                                        'filial_nueva'])


def detalle_vuelos(d, itinerario, res):
    """Carga a bordo, filial y costo de cada vuelo (el costo usa el ex-fuel de la filial que lo opera)."""
    carga = defaultdict(float)
    for c in res['solucion']['cargas']:
        carga[(c['k'], c['s'])] += c['x']
    filas = []
    for k, ls in itinerario.items():
        for l in sorted(ls, key=lambda x: x['pos']):
            op = filial_de(l, d['operador'][k])
            e = l['tramo']
            costo = (d['combustible_h'] + d['exf'][op]) * d['tau'][e] + d['tasa'][e[1]]
            filas.append({'aeronave': k, 'operador_base': d['operador'][k], 'filial': op, 'posicion': l['pos'],
                          'origen': e[0], 'destino': e[1], 'dia_salida': int(l['t_dep'] // 24) + 1,
                          'hora_salida': l['t_dep'], 'hora_llegada': l['t_arr'],
                          'carga_a_bordo_t': round(carga[(k, l['pos'])], 3), 'payload_t': d['payload'][k],
                          'costo_vuelo_usd': round(costo, 2)})
    return pd.DataFrame(filas)


def agregar_kpis_operador(kpis, v, itinerario, d):
    """KPIs propios del cambio de operador (los entrega el validador, que es el juez independiente)."""
    por_filial = defaultdict(int)
    for k, ls in itinerario.items():
        for l in ls:
            por_filial[filial_de(l, d['operador'][k])] += 1
    kpis['cambios_operador'] = v['kpis']['interchanges']
    kpis['vuelos_otra_filial'] = v['kpis']['vuelos_otra_filial']
    kpis['vuelos_por_filial'] = dict(sorted(por_filial.items()))
    # KPIs tal como los calcula el validador (misma fuente y definiciones que los de la solución final, p. ej. el
    # factor de ocupación CTK/ACTK); son los que deben usarse para comparar contra el modelo
    kpis['kpis_validador'] = {c: x for c, x in v['kpis'].items() if c != 'satisfaccion_demanda_diaria'}
    return kpis


def main():
    t0 = time.time()
    d = cargar_datos_v3()

    # ---- etapa 1: programación miope (v2, sin cambios)
    # Si con el bono MU la política deja frecuencias sin cubrir, se sube el bono por una grilla fija (el caso base debe
    # cumplir las 25 frecuencias, igual que el modelo final). Con las partidas de v2 (CB_PARTIDAS=base) MU=20.000 ya las
    # cubre todas, así que ahí nunca se sube y el itinerario es el de v2.
    mu_usado = MU
    itinerario, sin_cubrir = construir_caso_base(d, con_frecuencias=FRECUENCIAS, mu=mu_usado)
    for mu_prueba in [m for m in (30000.0, 50000.0, 100000.0, 200000.0, 500000.0) if m > MU]:
        if not (sin_cubrir and FRECUENCIAS):
            break
        print(f'  [intento descartado] con MU={mu_usado:,.0f} la política deja vuelos mínimos sin programar '
              f'{dict(sin_cubrir)} (ruta, vuelos faltantes); se prueba MU={mu_prueba:,.0f}', flush=True)
        mu_usado = mu_prueba
        itinerario, sin_cubrir = construir_caso_base(d, con_frecuencias=FRECUENCIAS, mu=mu_usado)
    if sin_cubrir:
        print('ATENCIÓN: frecuencias que no se lograron programar:', sin_cubrir)
    elif FRECUENCIAS:
        print(f"Itinerario elegido: cubre las {len(d['freq'])} rutas con frecuencia mínima "
              f"({sum(d['freq'].values())} vuelos semanales) con MU={mu_usado:,.0f}", flush=True)
    print(f"Itinerario: {len(itinerario)} aviones, {sum(len(v) for v in itinerario.values())} vuelos, MU={mu_usado:,.0f} "
          f"({time.time() - t0:.1f} s). Asignando carga con operador fijo...", flush=True)

    # ---- etapa 2: carga óptima con el operador base (= caso base v2)
    res0, v0 = resolver_carga_y_validar(itinerario)
    kpis_fijo = calcular_kpis(d, itinerario, v0, res0)
    agregar_kpis_operador(kpis_fijo, v0, itinerario, d)
    print(f"Caso base con operador fijo: margen {v0['margen']:,.0f} USD | validador {'OK' if v0['ok'] else 'FALLA'} "
          f"({time.time() - t0:.0f} s)", flush=True)

    # ---- etapas 3 a 5: cambio de operador
    if CAMBIO_OPERADOR:
        print(f"Asignando filiales (borde {'base' if BORDE_BASE else 'flexible'}, hasta {PASOS} pasos)...", flush=True)
        mejor, hist = asignar_cambio_operador(itinerario, res0, v0)
    else:
        mejor, hist = {'itinerario': itinerario, 'res': res0, 'v': v0}, []
    it_final, res, v = mejor['itinerario'], mejor['res'], mejor['v']

    kpis = calcular_kpis(d, it_final, v, res)
    agregar_kpis_operador(kpis, v, it_final, d)
    kpis['frecuencias_sin_cubrir'] = {f'{o}-{dd}': f for (o, dd), f in sin_cubrir.items()}
    kpis['config'] = {'frecuencias_exigidas': FRECUENCIAS, 'cambio_operador': CAMBIO_OPERADOR, 'partidas': PARTIDAS,
                      'borde': 'base' if BORDE_BASE else 'flexible', 'max_tramos_rotacion': MAX_TRAMOS, 'mu_usd': mu_usado,
                      'esperas_h': ESPERAS, 'pasos_max': PASOS, 'solver': os.environ['SOLVER']}
    kpis['margen_operador_fijo_usd'] = round(v0['margen'])
    kpis['mejora_por_cambio_operador_usd'] = round(v['margen'] - v0['margen'])
    kpis['tiempo_total_s'] = round(time.time() - t0, 1)
    print(json.dumps(kpis, indent=2, ensure_ascii=False))

    # ---- salidas
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    pd.DataFrame([{'aeronave': k, 'operador_base': d['operador'][k], 'filial': filial_de(l, d['operador'][k]),
                   'posicion': l['pos'], 'origen': l['tramo'][0], 'destino': l['tramo'][1],
                   'hora_salida': l['t_dep'], 'hora_llegada': l['t_arr']}
                  for k, ls in it_final.items() for l in sorted(ls, key=lambda x: x['pos'])]
                 ).to_csv(SALIDA + '_itinerario.csv', index=False)
    with open(SALIDA + '_kpis.json', 'w', encoding='utf-8') as f:
        json.dump(kpis, f, indent=2, ensure_ascii=False)
    kpis_fijo['config'] = dict(kpis['config'], cambio_operador=False)
    with open(SALIDA + '_kpis_operador_fijo.json', 'w', encoding='utf-8') as f:
        json.dump(kpis_fijo, f, indent=2, ensure_ascii=False)
    filas = []
    for nombre, kp in (('operador_fijo', kpis_fijo), ('cambio_operador', kpis)):
        fila = {c: x for c, x in kp.items() if c not in ('violaciones', 'frecuencias_sin_cubrir', 'config', 'kpis_validador')}
        fila['vuelos_por_filial'] = json.dumps(fila['vuelos_por_filial'])
        filas.append({'version': nombre, **fila})
    pd.DataFrame(filas).to_csv(SALIDA + '_kpis.csv', index=False)
    detalle_vuelos(d, it_final, res).to_csv(SALIDA + '_vuelos_detalle.csv', index=False)
    pd.DataFrame([{'aeronave': k, 'base_mantenimiento': d['mant'][k][0],
                   'partida': (sorted(ls, key=lambda x: x['pos'])[0]['tramo'][0] if ls else '(sin vuelos)')}
                  for k, ls in sorted((k, it_final.get(k, [])) for k in d['K'])]
                 ).to_csv(SALIDA + '_partidas.csv', index=False)
    li = lista_interchanges(it_final, d)
    li.to_csv(SALIDA + '_interchanges.csv', index=False)
    if len(li) != v['kpis']['interchanges']:
        print(f"AVISO: la lista de interchanges ({len(li)}) no coincide con la del validador "
              f"({v['kpis']['interchanges']}); el número oficial es el del validador.")
    if hist:
        pd.DataFrame(hist).to_csv(SALIDA + '_historial.csv', index=False)
    print(f"Listo ({kpis['tiempo_total_s']:.0f} s). Archivos con el prefijo {SALIDA}")


if __name__ == '__main__':
    main()
