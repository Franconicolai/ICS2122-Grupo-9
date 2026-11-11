"""Asignación global de carga sobre itinerarios individuales ya construidos.

Este módulo amplía la segunda etapa de la red espacio-tiempo. Los vuelos quedan
fijos y se decide, para cada commodity diario, cuántas toneladas:

* viajan directamente;
* continúan en vuelos consecutivos del mismo avión (``through``);
* cambian a otro avión, incluso de otro operador (transbordo).

El problema es un flujo multicommodity sobre los vuelos reales. Las conexiones
``through`` no pagan handling adicional ni requieren ``TTR_H``. Un transbordo
solo existe si ambos operadores tienen soporte en el aeropuerto, el aeropuerto
no es técnico y se respeta el tiempo mínimo de transbordo.
"""

import json
import os
import pickle
import sys
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import yaml

from solver_util import filas_a_matriz, resolver_matriz


RAW = os.environ.get(
    "RAW",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw data"),
)
TTR_H = float(os.environ.get("TTR_H", 2.0))
TLIM_CARGA = float(os.environ.get("TLIM_CARGA", 300))
GAP_CARGA = float(os.environ.get("GAP_CARGA", 0.001))
TOL = 1e-7


def cargar_datos_carga(ruta_raw=RAW):
    """Lee únicamente los datos que necesita el flujo global de carga."""
    rd = lambda nombre: pd.read_csv(os.path.join(ruta_raw, nombre))
    flota = rd("fleet.csv")
    demanda_semanal = rd("demand_weekly.csv")
    demanda_diaria = rd("demand_daily.csv")
    costos = rd("cost_params.csv")
    intercambio = rd("interchange_airports.csv")
    with open(os.path.join(ruta_raw, "ops_rules.yaml"), encoding="utf-8") as archivo:
        reglas = yaml.safe_load(archivo)

    operadores = dict(zip(flota.aircraft_id, flota.operator))
    payload = dict(zip(flota.aircraft_id, flota.payload_tons.astype(float)))
    handling = {
        r.operator: float(r.value)
        for r in costos.itertuples()
        if r.param == "handling_usd_per_ton" and r.operator != "ALL"
    }
    soporte = {
        (r.airport, operador): bool(getattr(r, operador))
        for r in intercambio.itertuples()
        for operador in intercambio.columns
        if operador != "airport"
    }
    dias = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    demanda = {
        (r.origin, r.dest, dia): float(getattr(r, columna))
        for r in demanda_diaria.itertuples()
        for dia, columna in enumerate(dias, start=1)
        if float(getattr(r, columna)) > TOL
    }
    return {
        "operador": operadores,
        "payload": payload,
        "handling": handling,
        "soporte": soporte,
        "tecnicos": set(reglas["technical_stop_airports"]),
        "min_ton": float(reglas["min_tons_per_extra_stop"]),
        "tat_full_h": float(reglas["tat_minutes"]["full_in_full_out"]) / 60.0,
        "tarifa": {
            (r.origin, r.dest): float(r.tariff_usd_per_kg)
            for r in demanda_semanal.itertuples()
        },
        "demanda": demanda,
    }


def construir_vuelos(itinerario, datos):
    """Normaliza los vuelos individuales y les asigna un identificador estable."""
    vuelos = {}
    por_avion_pos = {}
    ordenados = []
    for avion, legs in itinerario.items():
        for leg in legs:
            ordenados.append((float(leg["t_dep"]), avion, int(leg["pos"]), leg))
    for fid, (_, avion, posicion, leg) in enumerate(sorted(ordenados)):
        origen, destino = tuple(leg["tramo"])
        vuelos[fid] = {
            "id": fid,
            "k": avion,
            "s": posicion,
            "op": datos["operador"][avion],
            "o": origen,
            "d": destino,
            "dep": float(leg["t_dep"]),
            "arr": float(leg["t_arr"]),
            "payload": float(datos["payload"][avion]),
        }
        por_avion_pos[(avion, posicion)] = fid
    return vuelos, por_avion_pos


def construir_conexiones(vuelos, por_avion_pos, datos, ttr_h):
    """Construye arcos through y de transbordo entre vuelos compatibles."""
    conexiones = []
    vistos = set()

    # Through: solo entre posiciones inmediatamente consecutivas del mismo avión.
    for f in vuelos.values():
        siguiente = por_avion_pos.get((f["k"], f["s"] + 1))
        if siguiente is None:
            continue
        g = vuelos[siguiente]
        if f["d"] == g["o"] and g["dep"] + TOL >= f["arr"]:
            conexiones.append(
                {"id": len(conexiones), "desde": f["id"], "hacia": g["id"],
                 "tipo": "through", "aeropuerto": f["d"]}
            )
            vistos.add((f["id"], g["id"]))

    llegadas = defaultdict(list)
    salidas = defaultdict(list)
    for f in vuelos.values():
        llegadas[f["d"]].append(f)
        salidas[f["o"]].append(f)

    # Transbordo: siempre cambia el avión; puede conservar o cambiar operador.
    for aeropuerto, entrantes in llegadas.items():
        if aeropuerto in datos["tecnicos"]:
            continue
        for f in entrantes:
            for g in salidas.get(aeropuerto, []):
                if f["k"] == g["k"] or (f["id"], g["id"]) in vistos:
                    continue
                if g["dep"] + TOL < f["arr"] + ttr_h:
                    continue
                if not datos["soporte"].get((aeropuerto, f["op"]), False):
                    continue
                if not datos["soporte"].get((aeropuerto, g["op"]), False):
                    continue
                conexiones.append(
                    {"id": len(conexiones), "desde": f["id"], "hacia": g["id"],
                     "tipo": "transfer", "aeropuerto": aeropuerto}
                )
    return conexiones


def _alcanzables(iniciales, adyacencia, campo_destino):
    """Función _alcanzables: [Descripción pendiente]."""
    vistos = set(iniciales)
    cola = deque(iniciales)
    while cola:
        actual = cola.popleft()
        for conexion in adyacencia.get(actual, []):
            siguiente = conexion[campo_destino]
            if siguiente not in vistos:
                vistos.add(siguiente)
                cola.append(siguiente)
    return vistos


def resolver_asignacion(itinerario, datos, ttr_h=TTR_H, tlim=TLIM_CARGA,
                        gap=GAP_CARGA, log=False):
    """Resuelve el flujo de carga para itinerarios fijos.

    El valor de ``ttr_h`` es un parámetro operacional. El valor por defecto es
    provisional y puede cambiarse con la variable de entorno ``TTR_H``.
    """
    vuelos, por_avion_pos = construir_vuelos(itinerario, datos)
    conexiones = construir_conexiones(vuelos, por_avion_pos, datos, ttr_h)
    salientes = defaultdict(list)
    entrantes = defaultdict(list)
    for conexion in conexiones:
        salientes[conexion["desde"]].append(conexion)
        entrantes[conexion["hacia"]].append(conexion)

    fuentes = defaultdict(list)
    destinos = defaultdict(list)
    for fid, vuelo in vuelos.items():
        fuentes[(vuelo["o"], int(vuelo["dep"] // 24) + 1)].append(fid)
        destinos[vuelo["d"]].append(fid)

    hacia_adelante = {}
    hacia_atras = {}
    for origen_dia, fs in fuentes.items():
        hacia_adelante[origen_dia] = _alcanzables(fs, salientes, "hacia")
    for destino, fs in destinos.items():
        hacia_atras[destino] = _alcanzables(fs, entrantes, "desde")

    q_vuelos = {}
    q_conexiones = {}
    for q, demanda in datos["demanda"].items():
        if demanda <= TOL or (q[0], q[1]) not in datos["tarifa"]:
            continue
        relevantes = hacia_adelante.get((q[0], q[2]), set()) & hacia_atras.get(q[1], set())
        if not relevantes:
            continue
        arcos = [
            conexion["id"]
            for conexion in conexiones
            if conexion["desde"] in relevantes and conexion["hacia"] in relevantes
        ]
        q_vuelos[q] = sorted(relevantes)
        q_conexiones[q] = arcos

    lb, ub, obj, integ = [], [], [], []
    idx_x, idx_b, idx_a, idx_c, idx_l, idx_pair = {}, {}, {}, {}, {}, {}

    def variable(lim_inf, lim_sup, coef_obj, entera=False):
        """Función variable: [Descripción pendiente]."""
        indice = len(lb)
        lb.append(lim_inf); ub.append(lim_sup); obj.append(coef_obj); integ.append(bool(entera))
        return indice

    vuelos_con_carga_posible = set()
    for q, fids in q_vuelos.items():
        demanda = datos["demanda"][q]
        tarifa = 1000.0 * datos["tarifa"][(q[0], q[1])]
        for fid in fids:
            vuelo = vuelos[fid]
            vuelos_con_carga_posible.add(fid)
            idx_x[q, fid] = variable(0, min(demanda, vuelo["payload"]), 0.0)
            if vuelo["o"] == q[0] and int(vuelo["dep"] // 24) + 1 == q[2]:
                idx_b[q, fid] = variable(0, demanda, -datos["handling"][vuelo["op"]])
            if vuelo["d"] == q[1]:
                idx_a[q, fid] = variable(0, demanda, tarifa)
        for cid in q_conexiones[q]:
            conexion = conexiones[cid]
            receptor = vuelos[conexion["hacia"]]
            costo = 0.0 if conexion["tipo"] == "through" else -datos["handling"][receptor["op"]]
            idx_c[q, cid] = variable(0, demanda, costo)

    for fid in sorted(vuelos_con_carga_posible):
        idx_l[fid] = variable(0, 1, 0.0, entera=True)

    # Si el TAT real es menor que 90 min, uno de los dos vuelos debe quedar bajo el umbral de lleno.
    pares_tat = []
    for avion, legs in itinerario.items():
        orden = sorted(legs, key=lambda leg: leg["pos"])
        for primero, segundo in zip(orden, orden[1:]):
            f = por_avion_pos[(avion, int(primero["pos"]))]
            g = por_avion_pos[(avion, int(segundo["pos"]))]
            if primero["tramo"][1] in datos["tecnicos"]:
                continue
            if float(segundo["t_dep"]) - float(primero["t_arr"]) < datos["tat_full_h"] - 1e-6:
                idx_pair[f, g] = variable(0, 1, 0.0, entera=True)
                pares_tat.append((f, g))
        if orden:
            ultimo, primero = orden[-1], orden[0]
            f = por_avion_pos[(avion, int(ultimo["pos"]))]
            g = por_avion_pos[(avion, int(primero["pos"]))]
            gap_ciclico = float(primero["t_dep"]) + 168.0 - float(ultimo["t_arr"])
            if (ultimo["tramo"][1] not in datos["tecnicos"]
                    and gap_ciclico < datos["tat_full_h"] - 1e-6):
                idx_pair[f, g] = variable(0, 1, 0.0, entera=True)
                pares_tat.append((f, g))

    rows = []
    por_carga_vuelo = defaultdict(list)
    entrada_conexion = defaultdict(list)
    salida_conexion = defaultdict(list)
    for (q, fid), j in idx_x.items():
        por_carga_vuelo[fid].append(j)
    for (q, cid), j in idx_c.items():
        conexion = conexiones[cid]
        salida_conexion[q, conexion["desde"]].append(j)
        entrada_conexion[q, conexion["hacia"]].append(j)

    # Balance de entrada y salida en cada vuelo para cada commodity.
    for q, fids in q_vuelos.items():
        for fid in fids:
            entrada = defaultdict(float)
            entrada[idx_x[q, fid]] += 1
            if (q, fid) in idx_b:
                entrada[idx_b[q, fid]] -= 1
            for j in entrada_conexion[q, fid]:
                entrada[j] -= 1
            rows.append((entrada, 0, 0, ("balance_entrada", q, fid)))

            salida = defaultdict(float)
            salida[idx_x[q, fid]] += 1
            if (q, fid) in idx_a:
                salida[idx_a[q, fid]] -= 1
            for j in salida_conexion[q, fid]:
                salida[j] -= 1
            rows.append((salida, 0, 0, ("balance_salida", q, fid)))

        embarques = {j: 1 for (qq, _), j in idx_b.items() if qq == q}
        rows.append((embarques, 0, datos["demanda"][q], ("demanda", q)))

    # Capacidad y mínimo de toneladas por vuelo cargado.
    for fid, j_l in idx_l.items():
        carga = defaultdict(float, {j: 1 for j in por_carga_vuelo[fid]})
        carga[j_l] -= vuelos[fid]["payload"]
        rows.append((carga, -np.inf, 0, ("capacidad", fid)))
        minimo = defaultdict(float, {j: 1 for j in por_carga_vuelo[fid]})
        minimo[j_l] -= datos["min_ton"]
        rows.append((minimo, 0, np.inf, ("minimo", fid)))

    # Disyunción para el TAT lleno-lleno de 90 minutos.
    for f, g in pares_tat:
        j_par = idx_pair[f, g]
        seguro_f = max(0.0, 0.95 * vuelos[f]["payload"] - 0.01)
        seguro_g = max(0.0, 0.95 * vuelos[g]["payload"] - 0.01)
        c1 = defaultdict(float, {j: 1 for j in por_carga_vuelo.get(f, [])})
        c1[j_par] -= vuelos[f]["payload"] - seguro_f
        rows.append((c1, -np.inf, seguro_f, ("tat_full_1", f, g)))
        c2 = defaultdict(float, {j: 1 for j in por_carga_vuelo.get(g, [])})
        c2[j_par] += vuelos[g]["payload"] - seguro_g
        rows.append((c2, -np.inf, vuelos[g]["payload"], ("tat_full_2", f, g)))

    if not idx_x:
        return {
            "ok": True,
            "estado": "Sin caminos factibles",
            "objetivo_carga": 0.0,
            "solucion": {"itinerario": itinerario, "cargas": [], "transferencias": [], "ttr_h": ttr_h},
            "estadisticas": {"toneladas_entregadas": 0.0, "flujo_through_t": 0.0,
                              "flujo_transferido_t": 0.0, "conexiones_transferencia_usadas": 0},
            "tamano": {"vuelos": len(vuelos), "conexiones": len(conexiones), "variables": 0, "restricciones": 0},
        }

    matriz, lo, hi = filas_a_matriz(rows, len(lb))
    resultado = resolver_matriz(lb, ub, obj, integ, matriz, lo, hi, tlim, gap, log=log)
    if resultado["sol"] is None:
        return {
            "ok": False,
            "estado": resultado["estado"],
            "objetivo_carga": None,
            "solucion": None,
            "estadisticas": {},
            "tamano": {"vuelos": len(vuelos), "conexiones": len(conexiones),
                       "variables": len(lb), "restricciones": len(rows)},
        }

    valores = resultado["sol"]
    flujo_conexion = {
        (q, cid): valores[j]
        for (q, cid), j in idx_c.items()
        if valores[j] > 1e-6
    }
    transfer_in = defaultdict(float)
    transfer_out = defaultdict(float)
    through_out = defaultdict(float)
    transferencias = []
    for (q, cid), toneladas in flujo_conexion.items():
        conexion = conexiones[cid]
        desde, hacia = vuelos[conexion["desde"]], vuelos[conexion["hacia"]]
        if conexion["tipo"] == "through":
            through_out[q, desde["id"]] += toneladas
        else:
            transfer_out[q, desde["id"]] += toneladas
            transfer_in[q, hacia["id"]] += toneladas
            transferencias.append({
                "q": q,
                "from_k": desde["k"], "from_s": desde["s"],
                "to_k": hacia["k"], "to_s": hacia["s"],
                "tons": float(toneladas), "airport": conexion["aeropuerto"],
                "from_operator": desde["op"], "to_operator": hacia["op"],
            })

    cargas = []
    directo = 0.0
    entregado = 0.0
    for (q, fid), j in idx_x.items():
        toneladas = valores[j]
        if toneladas <= 1e-6:
            continue
        vuelo = vuelos[fid]
        b = valores[idx_b[q, fid]] if (q, fid) in idx_b else 0.0
        a = valores[idx_a[q, fid]] if (q, fid) in idx_a else 0.0
        w = through_out[q, fid]
        directo += min(b, a)
        entregado += a
        cargas.append({
            "k": vuelo["k"], "s": vuelo["s"], "q": q,
            "x": float(toneladas), "b": float(b), "a": float(a), "w": float(w),
            "transfer_in": float(transfer_in[q, fid]),
            "transfer_out": float(transfer_out[q, fid]),
        })

    flujo_through = sum(
        toneladas for (q, cid), toneladas in flujo_conexion.items()
        if conexiones[cid]["tipo"] == "through"
    )
    flujo_transferido = sum(t["tons"] for t in transferencias)
    solucion = {
        "itinerario": itinerario,
        "cargas": cargas,
        "transferencias": transferencias,
        "ttr_h": float(ttr_h),
    }
    return {
        "ok": True,
        "estado": resultado["estado"],
        "objetivo_carga": resultado["obj"],
        "cota": resultado.get("cota"),
        "gap": resultado.get("gap"),
        "tiempo_s": resultado.get("tiempo"),
        "solucion": solucion,
        "estadisticas": {
            "toneladas_entregadas": float(entregado),
            "toneladas_directas": float(directo),
            "flujo_through_t": float(flujo_through),
            "flujo_transferido_t": float(flujo_transferido),
            "conexiones_transferencia_usadas": len(transferencias),
            "transferencias_entre_operadores": sum(
                t["from_operator"] != t["to_operator"] for t in transferencias
            ),
        },
        "tamano": {
            "vuelos": len(vuelos), "conexiones": len(conexiones),
            "commodities_con_camino": len(q_vuelos),
            "variables": len(lb), "variables_enteras": sum(integ), "restricciones": len(rows),
        },
    }


def asignar_carga_multitramos(itinerario, ruta_raw=RAW, ttr_h=TTR_H,
                              tlim=TLIM_CARGA, gap=GAP_CARGA, log=False):
    """Función asignar_carga_multitramos: [Descripción pendiente]."""
    datos = cargar_datos_carga(ruta_raw)
    return resolver_asignacion(itinerario, datos, ttr_h=ttr_h, tlim=tlim, gap=gap, log=log)


def _main(ruta_pickle):
    """Función _main: [Descripción pendiente]."""
    from validador import validar

    entrada = pickle.load(open(ruta_pickle, "rb"))
    itinerario = entrada["itinerario"]
    resultado = asignar_carga_multitramos(itinerario, log=os.environ.get("LOG_CARGA") == "1")
    base = ruta_pickle[:-4] if ruta_pickle.endswith(".pkl") else ruta_pickle
    if resultado["ok"]:
        resultado["validacion"] = validar(
            resultado["solucion"],
            {"ciclico": True, "tat_variable": True, "min_ton_escala": True,
             "mantenimiento": True, "frecuencias": True, "demanda_diaria": True,
             "dist_frecuencias": False, "transferencias": True, "ttr_h": TTR_H},
            ruta_raw=RAW,
        )
        filas_carga = []
        for registro in resultado["solucion"]["cargas"]:
            q = tuple(registro["q"])
            filas_carga.append({
                "avion": registro["k"], "pos": registro["s"],
                "origen_q": q[0], "destino_q": q[1], "dia_q": q[2],
                "a_bordo_t": registro["x"], "embarque_inicial_t": registro["b"],
                "entrega_t": registro["a"], "through_t": registro["w"],
                "transfer_in_t": registro.get("transfer_in", 0.0),
                "transfer_out_t": registro.get("transfer_out", 0.0),
            })
        pd.DataFrame(filas_carga).to_csv(base + "_carga_multitramos.csv", index=False)
        pd.DataFrame(resultado["solucion"]["transferencias"]).to_csv(
            base + "_transferencias.csv", index=False)
    with open(base + "_multitramos.pkl", "wb") as archivo:
        pickle.dump(resultado, archivo)
    with open(base + "_multitramos.json", "w", encoding="utf-8") as archivo:
        json.dump({k: v for k, v in resultado.items() if k != "solucion"}, archivo,
                  indent=2, ensure_ascii=False, default=str)
    print(json.dumps({k: v for k, v in resultado.items() if k != "solucion"},
                     indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python src/asignacion_carga_multitramos.py results/sol_dt2_poravion.pkl")
    _main(sys.argv[1])
