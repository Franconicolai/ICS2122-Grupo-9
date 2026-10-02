import os
import sys
import json
import shutil
import time
import subprocess
import glob
from pathlib import Path

# Configuración de variables de entorno por defecto
os.environ['SOLVER'] = os.environ.get('SOLVER', 'gurobi')
os.environ['DT'] = os.environ.get('DT', '1')
os.environ['TLIM'] = os.environ.get('TLIM', '1500')
os.environ['TLIM_OP'] = os.environ.get('TLIM_OP', '420')

# Agregar src al path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

def ejecutar_modelo():
    import red_espacio_tiempo as red
    import reoptimizacion_por_avion as reopt
    
    print("\n--- EJECUCIÓN DEL MODELO ---")
    version = input("Ingresa un nombre o versión para esta corrida (ej. v1_base): ").strip()
    if not version:
        version = "v_default"
        
    desc = input("Ingresa una breve descripción de esta corrida: ").strip()
    
    # Crear carpeta de versión
    results_dir = os.path.join("data", "results", version)
    os.makedirs(results_dir, exist_ok=True)
    
    pkl_agregada = os.path.join(results_dir, f"sol_dt{os.environ['DT']}.pkl")
    
    print(f"\n[Paso 1] Generando Red Agregada (Solver: {os.environ['SOLVER']}, Límite: {os.environ['TLIM']}s)")
    try:
        exito = red.run_paso_1(salida_pkl=pkl_agregada)
        if not exito or not os.path.exists(pkl_agregada):
            print("\n[!] ERROR: El Paso 1 no generó solución. Revisa el tiempo límite o el solver.")
            return
    except Exception as e:
        print(f"\n[!] Error en Paso 1: {e}")
        return

    print(f"\n[Paso 2] Reoptimización por Avión (Límite por op: {os.environ['TLIM_OP']}s)")
    try:
        # reoptimizacion genera todo usando pkl.replace('.pkl', '')
        reopt.main(pkl_agregada)
    except Exception as e:
        print(f"\n[!] Error en Paso 2: {e}")
        return
        
    print("\n[Paso 3] Consolidando y versionando resultados...")
    base_salida = pkl_agregada.replace('.pkl', '_poravion')
    mapa_renombres = {
        f"{base_salida}_kpis.json": "indicadores_kpi.json",
        f"{base_salida}_itinerario.csv": "itinerario_vuelos.csv",
        f"{base_salida}_carga_multitramos.csv": "carga_transportada.csv",
        f"{base_salida}_transferencias.csv": "transferencias_carga.csv",
        f"{base_salida}.pkl": "modelo_resuelto.pkl"
    }
    
    for origen, nuevo_nombre in mapa_renombres.items():
        if os.path.exists(origen):
            destino = os.path.join(results_dir, nuevo_nombre)
            shutil.move(origen, destino)
            
    # Generar resultados.json para la visualización
    generar_json_visualizacion(results_dir)
    
    # Guardar metadata de la versión
    metadata = {
        "version": version,
        "descripcion": desc,
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "solver": os.environ['SOLVER'],
        "dt": os.environ['DT'],
        "tlim": os.environ['TLIM']
    }
    with open(os.path.join(results_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
        
    print(f"\n¡Proceso completado! Resultados guardados en: {results_dir}")

def generar_json_visualizacion(results_dir):
    try:
        import pandas as pd
        ap = pd.read_csv(os.path.join('data', 'raw data', 'airports.csv'))
        fl = pd.read_csv(os.path.join('data', 'raw data', 'fleet.csv'))
        dem = pd.read_csv(os.path.join('data', 'raw data', 'demand_weekly.csv'))
        
        itin = pd.read_csv(os.path.join(results_dir, 'itinerario_vuelos.csv'))
        kpis = json.load(open(os.path.join(results_dir, 'indicadores_kpi.json'), 'r', encoding='utf-8'))
        carga = pd.read_csv(os.path.join(results_dir, 'carga_transportada.csv'))
        
        coords = {"AGT": {"lat": -25.52, "lon": -54.59}, "ANF": {"lat": -23.44, "lon": -70.44}, "ASU": {"lat": -25.24, "lon": -57.52}, "BAQ": {"lat": 10.89, "lon": -74.78}, "BEL": {"lat": -1.38, "lon": -48.48}, "BOG": {"lat": 4.7, "lon": -74.15}, "BRU": {"lat": 50.9, "lon": 4.48}, "BSB": {"lat": -15.87, "lon": -47.92}, "CCS": {"lat": 10.6, "lon": -66.99}, "CLO": {"lat": 3.54, "lon": -76.38}, "CNF": {"lat": -19.63, "lon": -43.97}, "CWB": {"lat": -25.53, "lon": -49.17}, "EZE": {"lat": -34.82, "lon": -58.54}, "FLN": {"lat": -27.67, "lon": -48.55}, "FRA": {"lat": 50.03, "lon": 8.57}, "GEO": {"lat": 6.5, "lon": -58.25}, "GIG": {"lat": -22.81, "lon": -43.25}, "GRU": {"lat": -23.43, "lon": -46.47}, "GUA": {"lat": 14.58, "lon": -90.53}, "GYE": {"lat": -2.16, "lon": -79.88}, "LAX": {"lat": 33.94, "lon": -118.41}, "LIM": {"lat": -12.02, "lon": -77.11}, "MAD": {"lat": 40.47, "lon": -3.56}, "MAO": {"lat": -3.04, "lon": -60.05}, "MDE": {"lat": 6.17, "lon": -75.43}, "MEX": {"lat": 19.44, "lon": -99.07}, "MIA": {"lat": 25.79, "lon": -80.29}, "MTY": {"lat": 25.78, "lon": -100.11}, "MVD": {"lat": -34.84, "lon": -56.03}, "NVT": {"lat": -26.88, "lon": -48.65}, "POA": {"lat": -29.99, "lon": -51.17}, "PTY": {"lat": 9.07, "lon": -79.38}, "REC": {"lat": -8.13, "lon": -34.92}, "SAL": {"lat": 13.44, "lon": -89.06}, "SCL": {"lat": -33.39, "lon": -70.79}, "SDQ": {"lat": 18.43, "lon": -69.67}, "SEA": {"lat": 47.45, "lon": -122.31}, "SID": {"lat": 16.74, "lon": -22.95}, "SJK": {"lat": -23.23, "lon": -45.87}, "SJO": {"lat": 9.99, "lon": -84.21}, "UIO": {"lat": -0.13, "lon": -78.36}, "VCP": {"lat": -23.01, "lon": -47.13}, "VIX": {"lat": -20.26, "lon": -40.29}, "ZAZ": {"lat": 41.67, "lon": -1.04}}
        
        ap['lat'] = ap['iata'].map(lambda x: coords.get(x, {}).get('lat', 0.0))
        ap['lon'] = ap['iata'].map(lambda x: coords.get(x, {}).get('lon', 0.0))
        
        rutas_unicas = itin[['origen', 'destino']].drop_duplicates().to_dict('records')
        
        resultados_vis = {
            "aeropuertos": ap.to_dict('records'),
            "flota": fl.to_dict('records'),
            "rutas_catalogo": rutas_unicas,
            "vuelos": [],
            "demanda_servida": [],
            "kpis": kpis.get("kpis", {})
        }
        
        for _, row in itin.iterrows():
            resultados_vis["vuelos"].append({
                "aeronave": row["avion"],
                "pos": int(row["pos"]) if "pos" in row else None,
                "operador": row["operador"] if "operador" in row else row["avion"][:3],
                "origen": row["origen"],
                "destino": row["destino"],
                "hora_salida": row["t_dep_h"],
                "hora_llegada": row["t_arr_h"],
                "carga_tons": row["carga_t"],
                "costo_usd": float(row["costo_usd"]) if "costo_usd" in row else 0.0,
                "handling_usd": float(row["handling_usd"]) if "handling_usd" in row else 0.0,
                "ingreso_usd": float(row["ingreso_usd"]) if "ingreso_usd" in row else 0.0
            })
            
        if 'origen_q' in carga.columns and 'destino_q' in carga.columns:
            agrupado = carga.groupby(['origen_q', 'destino_q'])['entrega_t'].sum().reset_index()
            for _, row in agrupado.iterrows():
                o, d = row['origen_q'], row['destino_q']
                disp = dem[(dem['origin'] == o) & (dem['dest'] == d)]
                disp_val = disp['tons_week'].sum() if not disp.empty else 0
                resultados_vis["demanda_servida"].append({
                    "origen": o, "destino": d,
                    "toneladas_servidas": row['entrega_t'],
                    "toneladas_disponibles": disp_val
                })
                
        json.dump(resultados_vis, open(os.path.join(results_dir, 'resultados.json'), 'w', encoding='utf-8'), indent=2)
    except Exception as e:
        print(f"[!] Advertencia: No se pudo generar resultados.json completo: {e}")

def visualizar_datos():
    results_base = os.path.join("data", "results")
    if not os.path.exists(results_base):
        print("No hay resultados generados todavía.")
        return
        
    versiones = [d for d in os.listdir(results_base) if os.path.isdir(os.path.join(results_base, d))]
    if not versiones:
        print("No hay versiones disponibles para visualizar.")
        return
        
    # Ordenar por fecha de modificación (más reciente primero)
    versiones.sort(key=lambda x: os.path.getmtime(os.path.join(results_base, x)), reverse=True)
        
    print("\n--- VERSIONES DISPONIBLES ---")
    for i, v in enumerate(versiones):
        meta_path = os.path.join(results_base, v, "metadata.json")
        desc = ""
        if os.path.exists(meta_path):
            try:
                m = json.load(open(meta_path, 'r', encoding='utf-8'))
                desc = f"- {m.get('descripcion', '')} ({m.get('fecha', '')})"
            except:
                pass
        print(f"{i+1}. {v} {desc}")
        
    sel = input("\nSelecciona el número de la versión a visualizar (Enter para cargar la más reciente automáticamente): ").strip()
    if sel == "":
        v_sel = versiones[0]
    else:
        try:
            idx = int(sel) - 1
            v_sel = versiones[idx]
        except:
            print("Selección inválida.")
            return
        
    # Copiamos el resultados.json de la versión seleccionada al actual para que index.html lo lea
    origen_json = os.path.join(results_base, v_sel, "resultados.json")
    if not os.path.exists(origen_json):
        print(f"La versión {v_sel} no contiene un archivo resultados.json para visualizar.")
        return
        
    destino_json = os.path.join(results_base, "visualizacion_actual.json")
    shutil.copy(origen_json, destino_json)
    
    print(f"\nIniciando visualizador para la versión: {v_sel}")
    subprocess.run([sys.executable, os.path.join("visualization", "main.py")])

def menu():
    print("==================================================")
    print("  OPTIMIZADOR DE RED ESPACIO-TIEMPO (FLOTA)       ")
    print("==================================================")
    print("1. Ejecutar Modelo")
    print("2. Visualizar Datos")
    print("3. Salir")
    opc = input("Selecciona una opción: ").strip()
    
    if opc == '1':
        ejecutar_modelo()
    elif opc == '2':
        visualizar_datos()
    elif opc == '3':
        sys.exit(0)
    else:
        print("Opción inválida.")

if __name__ == "__main__":
    menu()
