"""Reoptimización de itinerarios con cambios de operador intra-semana.

Este módulo implementa la "nueva habilidad" del modelo: un avión puede cambiar
de operador varias veces durante la semana, siempre que:

* el cambio ocurra en un aeropuerto *interchange* que dé soporte a ambos
  operadores;
* el avión termine la semana en el mismo aeropuerto en que la comenzó;
* el avión termine la semana con su operador inicial (puede volver a él con un
  cambio en tierra, sin necesidad de un vuelo del operador inicial).

La formulación es una red espacio-tiempo expandida por estado de operador para
**un avión a la vez**.  No se puede descomponer por operador cuando los aviones
pueden cambiar de operador: la decisión de qué avión vuela qué tramo deja de
pertenecer a un operador fijo.  Por eso el baseline sigue saliendo del pipeline
por operador (Paso 1 + Paso 2), pero la mejora con swaps se hace avión por avión
y se evalúa con el modelo global de carga (Paso 3).  Es una matheurística: el
MILP propone una rotación y el evaluador independiente (carga + validador)
decide si se acepta.  Nunca se acepta una solución peor que el baseline.

Variables principales por avión k:

  z[e,t,o]   binaria: el avión vuela el tramo e en el bloque t con operador o.
  g[a,t,o]   continua [0,1]: el avión espera en a del bloque t al t+1 con o.
  s[a,t,o1,o2] continua [0,1]: cambio de operador en a en el bloque t.
  u          binaria: el avión se usa (1) o queda ocioso (0).
  zl[e,t,o]  binaria: el vuelo lleva carga (>= 10 t).
  x[e,t,o]   continua: toneladas de carga directa.

La red es cíclica.  El corte semanal se fija en el operador inicial o_k y en
cualquier aeropuerto (el aeropuerto de inicio lo elige la optimización).  Para
mantener el corte limpio no se permiten cambios de operador en el bloque 0 ni
vuelos que lleguen al bloque T con un operador distinto del inicial; el avión
puede esperar un bloque y hacer el cambio antes de terminar.  Esto no cambia la
semántica pedida: el último vuelo puede ser de otro operador si el avión vuelve
a su operador inicial en tierra antes del cierre.
"""

from __future__ import annotations

import copy
import json
import math
import os
import pickle
import sys
import time
from collections import defaultdict
from datetime import datetime
from itertools import permutations

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asignacion_carga_multitramos import asignar_carga_multitramos
from validador import validar
from solver_util import filas_a_matriz, resolver_matriz

RAW = os.environ.get(
    "RAW",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw data"),
)
TTR_H = float(os.environ.get("TTR_H", 2.0))
TLIM_CARGA = float(os.environ.get("TLIM_CARGA", 300))
SWAP_TLIM = float(os.environ.get("SWAP_TLIM", 120))
SWAP_PASSES = int(os.environ.get("SWAP_PASSES", 2))
SWAP_MIN_MEJORA = float(os.environ.get("SWAP_MIN_MEJORA", 1.0))
SWAP_AMPLIAR = os.environ.get("SWAP_AMPLIAR", "0") == "1"
SWAP_PENALTY = float(os.environ.get("SWAP_PENALTY", 0.0))
SWAP_MAX_OD = int(os.environ.get("SWAP_MAX_OD", 15))
SWAP_TECNICOS = os.environ.get("SWAP_TECNICOS", "1") == "1"
# Peso del bono de precio sombra de capacidad.  El LP de carga da el valor
# marginal de una tonelada de capacidad; se usa para que el MILP del avión
# preserve vuelos valiosos para las conexiones, no solo carga directa.
SWAP_DUAL_WEIGHT = float(os.environ.get("SWAP_DUAL_WEIGHT", 0.5))
_HUBS_DEFAULT = "MIA,GRU,BOG,LIM,EZE,SCL,GIG,VCP,BRU,MAD,LAX,MEX,PTY,SID,MAO,MDE,UIO,SDQ,SJO"
_hubs_env = os.environ.get("SWAP_HUBS", "1").strip()
if _hubs_env in ("0", "1"):
    # Compatibilidad con el flag booleano que usa main.py: 1 = hubs por
    # defecto, 0 = sin hubs.  También se acepta una lista explícita.
    SWAP_HUBS = set(_HUBS_DEFAULT.split(",")) if _hubs_env == "1" else set()
else:
    SWAP_HUBS = {a.strip().upper() for a in _hubs_env.split(",") if a.strip()}


def _cfg_validacion(ttr_h: float) -> dict:
    return {
        "ciclico": True,
        "tat_variable": True,
        "min_ton_escala": True,
        "mantenimiento": True,
        "frecuencias": True,
        "demanda_diaria": True,
        "dist_frecuencias": False,
        "transferencias": True,
        "swaps": True,
        "ttr_h": ttr_h,
    }


def cargar_contexto(info: dict, DT: float, ruta_raw: str = RAW) -> dict:
    """Carga los datos auxiliares que necesita el modelo de swaps."""
    fl = pd.read_csv(os.path.join(ruta_raw, "fleet.csv"))
    mt = pd.read_csv(os.path.join(ruta_raw, "maintenance.csv"))
    intercambio = pd.read_csv(os.path.join(ruta_raw, "interchange_airports.csv"))
    reglas = yaml.safe_load(open(os.path.join(ruta_raw, "ops_rules.yaml"), encoding="utf-8"))
    cp = pd.read_csv(os.path.join(ruta_raw, "cost_params.csv"))

    T = int(info["T"])
    ops = list(info["ops"])
    ini = datetime.fromisoformat(str(reglas["week_start"]))
    tecnicos = set(reglas["technical_stop_airports"])
    minton = float(reglas["min_tons_per_extra_stop"])

    op_initial = dict(zip(fl.aircraft_id, fl.operator))
    payload = dict(zip(fl.aircraft_id, fl.payload_tons.astype(float)))
    mant = {}
    for r in mt.itertuples():
        h0 = (datetime.fromisoformat(r.start_datetime) - ini).total_seconds() / 3600
        h1 = (datetime.fromisoformat(r.end_datetime) - ini).total_seconds() / 3600
        mant[r.aircraft_id] = (
            r.airport,
            [t % T for t in range(int(math.floor(h0 / DT)), int(math.ceil(h1 / DT)))],
        )

    soporte = {}
    for r in intercambio.itertuples():
        for op in ops:
            if op in intercambio.columns:
                soporte[(r.airport, op)] = bool(int(getattr(r, op)))

    handling = {
        r.operator: float(r.value)
        for r in cp.itertuples()
        if r.param == "handling_usd_per_ton" and r.operator != "ALL"
    }

    aeropuertos = sorted({a for op in ops for e in info["legs_op"][op] for a in e})
    airport_set = set(aeropuertos)
    costo = {}
    for op in ops:
        for e in info["legs_op"][op]:
            costo[(op, e)] = (
                (info["fuel_h"] + info["c"][("ex_fuel_usd_per_block_hour", op)]) * info["tau"][e]
                + info["fee"][e[1]]
            )
    carga_ok = {
        e: (e in info["tarifa"] and e[0] not in tecnicos and e[1] not in tecnicos)
        for op in ops
        for e in info["legs_op"][op]
    }

    swap_pairs = {}
    for a in aeropuertos:
        pares = []
        for o1, o2 in permutations(ops, 2):
            if not (soporte.get((a, o1), False) and soporte.get((a, o2), False)):
                continue
            if not SWAP_TECNICOS and a in tecnicos:
                continue
            pares.append((o1, o2))
        if pares:
            swap_pairs[a] = pares

    return {
        "T": T,
        "DT": DT,
        "ops": ops,
        "op_initial": op_initial,
        "payload": payload,
        "mant": mant,
        "soporte": soporte,
        "handling": handling,
        "tecnicos": tecnicos,
        "minton": minton,
        "aeropuertos": aeropuertos,
        "airport_set": airport_set,
        "costo": costo,
        "carga_ok": carga_ok,
        "swap_pairs": swap_pairs,
        "legs_op": info["legs_op"],
        "dur": info["dur"],
        "tau": info["tau"],
        "tarifa": info["tarifa"],
        "dem": info["dem"],
        "fmin": info["fmin"],
        "ruta_raw": ruta_raw,
    }


def _residual(k: str, itinerario: dict, multi: dict, ctx: dict):
    """Demanda y frecuencia que quedan disponibles para el avión k."""
    dem_res = {q: float(d) for q, d in ctx["dem"].items()}
    servido_otros = defaultdict(float)
    for reg in multi["solucion"]["cargas"]:
        if reg["k"] == k:
            continue
        servido_otros[tuple(reg["q"])] += float(reg["a"])
    for q in list(dem_res):
        dem_res[q] = max(0.0, dem_res[q] - servido_otros.get(q, 0.0))

    freq_otros = defaultdict(int)
    for kk, legs in itinerario.items():
        if kk == k:
            continue
        for l in legs:
            freq_otros[tuple(l["tramo"])] += 1
    freq_res = {
        od: max(0.0, float(f) - freq_otros.get(od, 0))
        for od, f in ctx["fmin"].items()
    }
    return dem_res, freq_res


def _candidatos(k: str, itinerario: dict, ctx: dict, dem_res: dict, freq_res: dict):
    """Conjunto manejable de aeropuertos y tramos candidatos para un avión.

    Se parte de los aeropuertos de la rotación baseline, el aeropuerto de
    mantenimiento, los hubs de intercambio y los extremos de la demanda/frecuencia
    residual más valiosa.  Con SWAP_AMPLIAR=1 se usan todos los tramos.
    """
    if SWAP_AMPLIAR:
        aps = set(ctx["airport_set"])
    else:
        aps = set()
        for l in itinerario.get(k, []):
            aps.update(l["tramo"])
        aps.add(ctx["mant"][k][0])
        aps |= {a for a in SWAP_HUBS if a in ctx["airport_set"]}

        items = []
        for q, d in dem_res.items():
            if d <= ctx["minton"] - 1e-6:
                continue
            e = (q[0], q[1])
            valor = d * float(ctx["tarifa"].get(e, 0.0))
            items.append((valor, q))
        items.sort(key=lambda x: x[0], reverse=True)
        for _, q in items[: max(1, SWAP_MAX_OD)]:
            aps.add(q[0])
            aps.add(q[1])
        for od, f in freq_res.items():
            if f > 0:
                aps.add(od[0])
                aps.add(od[1])

    cand = set()
    for op in ctx["ops"]:
        for e in ctx["legs_op"][op]:
            if e[0] in aps and e[1] in aps:
                cand.add(e)
    return aps, cand


def _extraer_legs(k: str, r: dict, idx: dict, ctx: dict) -> tuple[list, int]:
    x = r["sol"]
    fs = []
    for key, j in idx.items():
        if key[0] == "z" and x[j] > 0.5:
            _, e, t, o = key
            fs.append((t, e, o))
    fs.sort(key=lambda z: (z[0], z[1]))
    legs = [
        {
            "pos": p,
            "tramo": e,
            "t_dep": t * ctx["DT"],
            "t_arr": t * ctx["DT"] + ctx["tau"][e],
            "operador": o,
        }
        for p, (t, e, o) in enumerate(fs, start=1)
    ]
    # Los swaps efectivos se leen de la secuencia de operadores de los vuelos,
    # no de las variables s (que pueden tener circulaciones de coste cero).
    secuencia = [ctx["op_initial"][k]] + [l["operador"] for l in legs] + [ctx["op_initial"][k]]
    n_swaps = sum(1 for a, b in zip(secuencia, secuencia[1:]) if a != b)
    return legs, n_swaps


def _duales_lp(
    itinerario: dict,
    ctx: dict,
    ttr_h: float = TTR_H,
    tlim_carga: float = TLIM_CARGA,
    log: bool = False,
) -> dict:
    """Precios sombra de capacidad de la relajación LP del modelo de carga.

    El MILP de carga tiene variables enteras para el mínimo de 10 t y la regla
    lleno-lleno, por lo que no entrega duales.  La relajación LP sí: su dual de
    capacidad es el valor marginal de una tonelada adicional en cada vuelo.
    Se usa como señal para que el MILP del avión no destruya conexiones valiosas.
    """
    lp = asignar_carga_multitramos(
        itinerario,
        ruta_raw=ctx["ruta_raw"],
        ttr_h=ttr_h,
        tlim=tlim_carga,
        log=log,
        relax=True,
        return_duals=True,
    )
    if not lp.get("ok") or not lp.get("duales_capacidad"):
        return {}
    ordenados = []
    for avion, legs in itinerario.items():
        for leg in legs:
            ordenados.append((float(leg["t_dep"]), avion, int(leg["pos"])))
    ordenados.sort()
    fid_a_ks = {fid: (avion, pos) for fid, (_, avion, pos) in enumerate(ordenados)}
    duales = {}
    for fid, d in lp["duales_capacidad"].items():
        ks = fid_a_ks.get(fid)
        if ks is None:
            continue
        # Los duales negativos son ruido numérico o capacidad no binding.
        duales[ks] = max(0.0, float(d))
    return duales


def resolver_avion(
    k: str,
    itinerario: dict,
    multi: dict,
    ctx: dict,
    tlim: float = SWAP_TLIM,
    log: bool = False,
    duales: dict | None = None,
) -> dict | None:
    """Resuelve la red espacio-tiempo con estados de operador para el avión k."""
    T = ctx["T"]
    DT = ctx["DT"]
    ops = ctx["ops"]
    op0 = ctx["op_initial"][k]
    Q = ctx["payload"][k]
    am, W = ctx["mant"][k]
    dem_res, freq_res = _residual(k, itinerario, multi, ctx)
    aps, cand = _candidatos(k, itinerario, ctx, dem_res, freq_res)

    # Bono de precio sombra: si el vuelo baseline (mismo tramo y bloque) tenía
    # capacidad escasa en el LP de carga, preservarlo vale aproximadamente
    # dual * payload.  Se aplica a cualquier operador que vuele ese vuelo, que
    # es justamente lo que permite el swap.
    bono_z = defaultdict(float)
    if duales:
        for l in itinerario.get(k, []):
            e = tuple(l["tramo"])
            t = int(round(float(l["t_dep"]) / DT))
            d = duales.get((k, int(l["pos"])), 0.0)
            if d > 0:
                bono_z[(e, t)] += SWAP_DUAL_WEIGHT * d * Q

    lb, ub, obj, integ, idx = [], [], [], [], {}

    def var(key, l, u, o, i):
        idx[key] = len(lb)
        lb.append(l)
        ub.append(u)
        obj.append(o)
        integ.append(i)

    var(("u",), 0, 1, 0.0, 1)

    modo = os.environ.get("SWAP_MODO", "auto").lower()
    if modo == "auto":
        # En aviones con rotación baseline, el modo restringido evita que el
        # MILP invente vuelos que el modelo global de carga luego no usa.  En
        # aviones ociosos sí conviene explorar la red amplia.
        modo = "amplio" if not itinerario.get(k) else "baseline"

    mandatory = []
    if modo == "baseline":
        # Modo conservador: se conservan los mismos vuelos (tramo y bloque) del
        # baseline y solo se decide con qué operador se vuela cada uno.  Es el
        # núcleo de la nueva habilidad y es mucho más pequeño y estable.
        vuelos_base = []
        for l in itinerario.get(k, []):
            e = tuple(l["tramo"])
            t = int(round(float(l["t_dep"]) / DT))
            vuelos_base.append((e, t))
        aps = {a for e, t in vuelos_base for a in e} | {am}
        cand = {e for e, t in vuelos_base}
        for e, t in vuelos_base:
            opciones = []
            dur = ctx["dur"][e]
            if t + dur > T:
                continue
            for o in ops:
                if e not in ctx["legs_op"][o]:
                    continue
                if t + dur == T and o != op0:
                    continue
                var(("z", e, t, o), 0, 1, -ctx["costo"][(o, e)] + bono_z.get((e, t), 0.0), 1)
                opciones.append(o)
                q = (e[0], e[1], int(t * DT // 24))
                if ctx["carga_ok"].get(e, False) and dem_res.get(q, 0.0) > ctx["minton"] - 1e-6:
                    var(("zl", e, t, o), 0, 1, 0.0, 1)
                    var(
                        ("x", e, t, o),
                        0,
                        Q,
                        1000.0 * ctx["tarifa"][e] - ctx["handling"][o],
                        0,
                    )
            mandatory.append((e, t, opciones))
    else:
        # Modo amplio: red espacio-tiempo completa sobre los candidatos.
        for o in ops:
            for e in ctx["legs_op"][o]:
                if e not in cand:
                    continue
                dur = ctx["dur"][e]
                costo = ctx["costo"][(o, e)]
                for t in range(T):
                    if t + dur > T:
                        continue
                    # El corte semanal debe quedar en el operador inicial.  Un vuelo
                    # que llega justo a T solo se permite con el operador inicial;
                    # con otro operador el avión debe llegar antes y cambiar en tierra.
                    if t + dur == T and o != op0:
                        continue
                    var(("z", e, t, o), 0, 1, -costo + bono_z.get((e, t), 0.0), 1)
                    q = (e[0], e[1], int(t * DT // 24))
                    if ctx["carga_ok"].get(e, False) and dem_res.get(q, 0.0) > ctx["minton"] - 1e-6:
                        var(("zl", e, t, o), 0, 1, 0.0, 1)
                        var(
                            ("x", e, t, o),
                            0,
                            Q,
                            1000.0 * ctx["tarifa"][e] - ctx["handling"][o],
                            0,
                        )

    # Espera en tierra.
    for a in aps:
        for t in range(T):
            for o in ops:
                var(("g", a, t, o), 0, 1, 0.0, 0)

    # Cambios de operador.  No se permiten en el bloque 0 para que el corte
    # semanal quede exactamente en el operador inicial.
    for a in aps:
        for t in range(1, T):
            for o1, o2 in ctx["swap_pairs"].get(a, []):
                var(("s", a, t, o1, o2), 0, 1, -SWAP_PENALTY, 1)

    rows = []
    sale = defaultdict(list)
    llega = defaultdict(list)
    sale_s = defaultdict(list)
    llega_s = defaultdict(list)
    corte = []
    por_t = defaultdict(list)
    por_q = defaultdict(list)
    por_od = defaultdict(list)
    por_swap_at = defaultdict(list)

    for key, j in idx.items():
        if key[0] == "z":
            _, e, t, o = key
            sale[(e[0], t, o)].append(j)
            llega[(e[1], (t + ctx["dur"][e]) % T, o)].append(j)
            if t + ctx["dur"][e] >= T:
                corte.append(j)
            por_t[t].append(j)
            por_od[e].append(j)
        elif key[0] == "s":
            _, a, t, o1, o2 = key
            sale_s[(a, t, o1)].append(j)
            llega_s[(a, t, o2)].append(j)
            por_swap_at[(a, t)].append(j)

    # Conservación de flujo en cada (aeropuerto, bloque, operador).
    for a in aps:
        for t in range(T):
            for o in ops:
                co = defaultdict(float)
                for j in llega[(a, t, o)]:
                    co[j] += 1
                co[idx[("g", a, (t - 1) % T, o)]] += 1
                for j in llega_s[(a, t, o)]:
                    co[j] += 1
                for j in sale[(a, t, o)]:
                    co[j] -= 1
                co[idx[("g", a, t, o)]] -= 1
                for j in sale_s[(a, t, o)]:
                    co[j] -= 1
                rows.append((co, 0, 0))

    # En modo restringido, cada vuelo baseline se conserva; solo cambia el
    # operador que lo ejecuta.
    for e, t, opciones in mandatory:
        co = {idx[("z", e, t, o)]: 1 for o in opciones}
        rows.append((co, 1, 1))

    # Corte semanal: el avión está en su operador inicial al comenzar/terminar.
    co = {idx[("g", a, T - 1, op0)]: 1 for a in aps}
    for j in corte:
        co[j] = co.get(j, 0.0) + 1
    co[idx[("u",)]] = -1
    rows.append((co, 0, 0))

    # Cierre de operador: al final de la semana no puede quedar flujo en otro
    # operador.  El avión debe volver a op0 en tierra antes del corte.
    for o in ops:
        if o == op0:
            continue
        co = {idx[("g", a, T - 1, o)]: 1 for a in aps}
        rows.append((co, 0, 0))

    # Mantenimiento: el avión está en su aeropuerto durante toda la ventana.
    for t in W:
        co = {idx[("g", am, t, o)]: 1 for o in ops}
        co[idx[("u",)]] = -1
        rows.append((co, 0, np.inf))

    # Un avión no puede despegar más de un vuelo por bloque; si u=0 no vuela.
    for t, js in por_t.items():
        co = {j: 1 for j in js}
        co[idx[("u",)]] = -1
        rows.append((co, -np.inf, 0))

    # A lo sumo un cambio de operador por aeropuerto y bloque.  Evita ciclos
    # artificiales de swaps en el mismo instante.
    for (a, t), js in por_swap_at.items():
        co = {j: 1 for j in js}
        co[idx[("u",)]] = -1
        rows.append((co, -np.inf, 0))

    # Carga: mínimo de 10 t si el vuelo se marca como cargado, capacidad y
    # demanda residual.  x <= dem_res*zl es una desigualdad válida que refuerza
    # la relajación lineal.
    for key, j in idx.items():
        if key[0] != "x":
            continue
        _, e, t, o = key
        q = (e[0], e[1], int(t * DT // 24))
        zl = idx[("zl", e, t, o)]
        rows.append(({j: 1, zl: -ctx["minton"]}, 0, np.inf))
        rows.append(({j: 1, zl: -Q}, -np.inf, 0))
        rows.append(({zl: 1, idx[("z", e, t, o)]: -1}, -np.inf, 0))
        rows.append(({j: 1, zl: -max(dem_res.get(q, 0.0), 0.0)}, -np.inf, 0))
        por_q[q].append(j)

    for q, js in por_q.items():
        rows.append(({j: 1 for j in js}, -np.inf, max(dem_res[q], 0.0)))

    for od, f in freq_res.items():
        if f > 0:
            rows.append(({j: 1 for j in por_od.get(od, [])}, f, np.inf))

    A, lo, hi = filas_a_matriz(rows, len(lb))
    r = resolver_matriz(lb, ub, obj, integ, A, lo, hi, tlim, gap=0.01, log=log)
    r.update(nvars=len(lb), nint=int(sum(integ)), nrows=len(rows))
    r["legs"] = _extraer_legs(k, r, idx, ctx) if r.get("sol") is not None else None
    r["aps"] = len(aps)
    r["cand"] = len(cand)
    r["dem_res"] = sum(dem_res.values())
    return r


def evaluar_itinerario(
    itinerario: dict,
    ctx: dict,
    ttr_h: float = TTR_H,
    tlim_carga: float = TLIM_CARGA,
    log: bool = False,
) -> dict | None:
    """Asigna carga global y valida.  Es el árbitro de la matheurística."""
    multi = asignar_carga_multitramos(
        itinerario,
        ruta_raw=ctx["ruta_raw"],
        ttr_h=ttr_h,
        tlim=tlim_carga,
        log=log,
    )
    if not multi.get("ok"):
        return None
    v = validar(multi["solucion"], _cfg_validacion(ttr_h), ruta_raw=ctx["ruta_raw"])
    return {
        "ok": bool(v["ok"]),
        "multi": multi,
        "validacion": v,
        "margen": float(v["margen"]),
    }


def mejorar_con_swaps(
    itinerario: dict,
    info: dict,
    DT: float,
    ruta_raw: str = RAW,
    ttr_h: float = TTR_H,
    tlim_carga: float = TLIM_CARGA,
    log: bool = False,
) -> dict:
    """Matheurística avión por avión con aceptación por margen validado.

    Se evalúa el baseline con el modelo global de carga.  Luego, para cada avión,
    se resuelve la red con estados de operador y se evalúa la rotación propuesta
    con el mismo modelo global.  Solo se acepta si el margen del validador
    mejora.  Se hacen varias pasadas hasta que no haya mejora.
    """
    ctx = cargar_contexto(info, DT, ruta_raw)
    base = evaluar_itinerario(itinerario, ctx, ttr_h=ttr_h, tlim_carga=tlim_carga, log=log)
    if base is None or not base["ok"]:
        return {"ok": False, "motivo": "baseline no factible para el evaluador global", "resumen": []}

    best_it = copy.deepcopy(itinerario)
    best_eval = base
    best_margin = base["margen"]
    resumen = []
    base_margin = best_margin

    solo = os.environ.get("SWAP_SOLO")
    if solo:
        aviones = [a.strip() for a in solo.split(",") if a.strip() in ctx["op_initial"]]
    else:
        aviones = list(ctx["op_initial"].keys())

    def uso(k):
        legs = best_it.get(k, [])
        return (len(legs), sum(ctx["tau"][tuple(l["tramo"])] for l in legs))

    aviones.sort(key=uso)  # primero los aviones ociosos / con menos horas

    for pasada in range(SWAP_PASSES):
        # Bucle de precios de carga: los duales se recalculan sobre el mejor
        # itinerario de la pasada anterior.  Así el MILP del avión internaliza
        # el valor de las conexiones que el modelo de carga global sí ve.
        t_dual = time.time()
        duales = _duales_lp(best_it, ctx, ttr_h=ttr_h, tlim_carga=tlim_carga, log=log)
        print(f"[swaps] pasada {pasada + 1}: precios de carga para "
              f"{len(duales)} vuelos con dual>0 ({time.time() - t_dual:.0f}s)", flush=True)
        mejoro = False
        for k in aviones:
            t0 = time.time()
            r = resolver_avion(k, best_it, best_eval["multi"], ctx, tlim=SWAP_TLIM, log=log,
                               duales=duales)
            if r is None or r.get("sol") is None:
                resumen.append({
                    "avion": k, "pasada": pasada + 1, "estado": "sin_solucion",
                    "tiempo_s": round(time.time() - t0, 1), "aceptado": False,
                })
                print(f"[swaps] {k}: sin solución en {time.time() - t0:.0f}s", flush=True)
                continue

            legs, n_swaps = r["legs"]
            base_legs = [
                (tuple(l["tramo"]), float(l["t_dep"]), l.get("operador", ctx["op_initial"][k]))
                for l in best_it.get(k, [])
            ]
            cand_legs = [(tuple(l["tramo"]), float(l["t_dep"]), l["operador"]) for l in legs]
            if cand_legs == base_legs:
                resumen.append({
                    "avion": k, "pasada": pasada + 1, "estado": r["estado"],
                    "operador_inicial": ctx["op_initial"][k],
                    "operador_final": ctx["op_initial"][k],
                    "vuelos": len(legs), "swaps": n_swaps,
                    "aps_candidatos": r["aps"], "tramos_candidatos": r["cand"],
                    "enteras": r["nint"], "restr": r["nrows"],
                    "objetivo_milp": r.get("obj"), "cota": r.get("cota"), "gap": r.get("gap"),
                    "tiempo_milp_s": round(r.get("tiempo", 0.0), 1),
                    "tiempo_total_s": round(time.time() - t0, 1),
                    "margen_candidato": best_margin, "delta_margen": 0.0,
                    "aceptado": False, "motivo": "sin_cambio",
                })
                print(f"[swaps] {k} p{pasada + 1}: sin cambio de vuelos/operador", flush=True)
                continue

            cand_it = copy.deepcopy(best_it)
            cand_it[k] = legs
            ev = evaluar_itinerario(cand_it, ctx, ttr_h=ttr_h, tlim_carga=tlim_carga, log=log)
            if ev is None or not ev["ok"]:
                resumen.append({
                    "avion": k, "pasada": pasada + 1, "estado": "candidato_infactible",
                    "tiempo_s": round(time.time() - t0, 1), "aceptado": False,
                })
                print(f"[swaps] {k}: candidato infactible; se rechaza", flush=True)
                continue

            delta = ev["margen"] - best_margin
            aceptado = delta > SWAP_MIN_MEJORA
            if aceptado:
                best_it = cand_it
                best_eval = ev
                best_margin = ev["margen"]
                mejoro = True

            op_final = legs[-1]["operador"] if legs else ctx["op_initial"][k]
            resumen.append({
                "avion": k,
                "pasada": pasada + 1,
                "estado": r["estado"],
                "operador_inicial": ctx["op_initial"][k],
                "operador_final": op_final,
                "vuelos": len(legs),
                "swaps": n_swaps,
                "aps_candidatos": r["aps"],
                "tramos_candidatos": r["cand"],
                "enteras": r["nint"],
                "restr": r["nrows"],
                "objetivo_milp": r.get("obj"),
                "cota": r.get("cota"),
                "gap": r.get("gap"),
                "tiempo_milp_s": round(r.get("tiempo", 0.0), 1),
                "tiempo_total_s": round(time.time() - t0, 1),
                "margen_candidato": ev["margen"],
                "delta_margen": delta,
                "aceptado": aceptado,
            })
            print(
                f"[swaps] {k} p{pasada + 1}: {r['estado']} | vuelos={len(legs)} swaps={n_swaps} "
                f"| margen {ev['margen']:,.0f} (delta {delta:+,.0f}) "
                f"| {'ACEPTADO' if aceptado else 'rechazado'} | {time.time() - t0:.0f}s",
                flush=True,
            )
        if not mejoro:
            break

    return {
        "ok": bool(best_eval["ok"]),
        "itinerario": best_it,
        "multi": best_eval["multi"],
        "validacion": best_eval["validacion"],
        "resumen": resumen,
        "margen_base": base_margin,
        "margen_final": best_margin,
        "mejora": best_margin - base_margin,
    }


if __name__ == "__main__":
    # Prueba manual: python src/swap_operadores.py <paso1.pkl> <modelo_resuelto.pkl>
    if len(sys.argv) < 3:
        print("Uso: python src/swap_operadores.py <paso1.pkl> <modelo_resuelto.pkl>")
        sys.exit(1)
    P = pickle.load(open(sys.argv[1], "rb"))  # noqa: F821
    M = pickle.load(open(sys.argv[2], "rb"))  # noqa: F821
    out = mejorar_con_swaps(M["itinerario"], P["info"], P["DT"], log=os.environ.get("LOG") == "1")
    print(json.dumps({k: v for k, v in out.items() if k not in ("itinerario", "multi", "validacion")},
                     indent=2, ensure_ascii=False, default=str))  # noqa: F821
