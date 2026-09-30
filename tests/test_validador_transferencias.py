import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from validador import validar


class ValidadorTransferenciasTest(unittest.TestCase):
    def solucion(self, salida_segundo=7.0):
        q = ("BOG", "VCP", 1)
        return {
            "itinerario": {
                "AC-03": [{"pos": 1, "tramo": ("BOG", "MIA"), "t_dep": 1.0, "t_arr": 4.67}],
                "AC-06": [{"pos": 1, "tramo": ("MIA", "VCP"), "t_dep": salida_segundo,
                           "t_arr": salida_segundo + 8.33}],
            },
            "cargas": [
                {"k": "AC-03", "s": 1, "q": q, "x": 7.6, "b": 7.6, "a": 0.0, "w": 0.0},
                {"k": "AC-06", "s": 1, "q": q, "x": 7.6, "b": 0.0, "a": 7.6, "w": 0.0},
            ],
            "transferencias": [{
                "q": q, "from_k": "AC-03", "from_s": 1, "to_k": "AC-06", "to_s": 1,
                "tons": 7.6, "airport": "MIA", "from_operator": "AND", "to_operator": "BRA",
            }],
            "ttr_h": 2.0,
        }

    def config(self):
        return {
            "ciclico": False, "tat_variable": False, "min_ton_escala": False,
            "mantenimiento": False, "frecuencias": False, "demanda_diaria": True,
            "dist_frecuencias": False, "transferencias": True, "ttr_h": 2.0,
        }

    def test_acepta_transferencia_balanceada(self):
        resultado = validar(self.solucion(), self.config(), ruta_raw=os.path.join(ROOT, "data", "raw data"))
        self.assertTrue(resultado["ok"], resultado["violaciones"])
        self.assertAlmostEqual(resultado["toneladas_entregadas"], 7.6, places=5)

    def test_detecta_incumplimiento_de_ttr(self):
        resultado = validar(
            self.solucion(salida_segundo=6.0), self.config(),
            ruta_raw=os.path.join(ROOT, "data", "raw data"))
        self.assertFalse(resultado["ok"])
        self.assertTrue(any("Ttr" in v for v in resultado["violaciones"]))


if __name__ == "__main__":
    unittest.main()
