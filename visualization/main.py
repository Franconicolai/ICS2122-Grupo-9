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
