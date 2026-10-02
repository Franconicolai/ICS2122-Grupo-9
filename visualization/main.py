"""
Módulo: visualization/main.py
Descripción: Script para iniciar el servidor local web y servir 
la interfaz gráfica interactiva en el navegador.
"""
import os
import sys
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8080

class VisualizationServer(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silenciar logs para mantener la consola limpia
        pass

    def do_GET(self):
        if self.path.endswith('/api/versions'):
            import json
            results_base = os.path.join("data", "results")
            versiones = []
            if os.path.exists(results_base):
                for d in os.listdir(results_base):
                    if os.path.isdir(os.path.join(results_base, d)):
                        meta_path = os.path.join(results_base, d, "metadata.json")
                        desc = ""
                        if os.path.exists(meta_path):
                            try:
                                m = json.load(open(meta_path, 'r', encoding='utf-8'))
                                desc = f"{m.get('descripcion', '')} ({m.get('fecha', '')})"
                            except:
                                pass
                        # check if it has resultados.json
                        if os.path.exists(os.path.join(results_base, d, "resultados.json")):
                            versiones.append({"id": d, "desc": desc})
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(versiones).encode('utf-8'))
            return
        return super().do_GET()

def start_server():
    # Establecer la carpeta de visualization (este directorio) como la raíz del servidor
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # Subir un nivel para que pueda servir ../data/resources
    # ¡Importante! Si servimos desde visualization, fetch('../data/...') fallará
    # a menos que el servidor se levante desde la raíz del proyecto.
    os.chdir("..")
    
    server_address = ('', PORT)
    try:
        httpd = HTTPServer(server_address, VisualizationServer)
        httpd.serve_forever()
    except Exception as e:
        print(f"Error iniciando servidor: {e}")

if __name__ == "__main__":
    print("\n==================================================")
    print("  SERVIDOR DE VISUALIZACIÓN LOGÍSTICA ")
    print("==================================================")
    
    # Levantar el servidor en un hilo secundario
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    url = f"http://localhost:{PORT}/visualization/"
    print(f"\nIniciando servidor local...")
    print(f"Abriendo interfaz gráfica en: {url}")
    webbrowser.open(url)
    
    print("\nLa interfaz gráfica está corriendo. Presiona Ctrl+C para apagar el servidor.")
    
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("\n\nApagando servidor web...")
        sys.exit(0)
