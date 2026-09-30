import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from asignacion_carga_multitramos import resolver_asignacion


def datos_base(soporte_bbb=True, tecnico=False):
    return {
        "operador": {"A1": "AND", "B1": "BRA"},
        "payload": {"A1": 100.0, "B1": 100.0},
        "handling": {"AND": 20.0, "BRA": 30.0},
        "soporte": {
            ("AAA", "AND"): True, ("AAA", "BRA"): True,
            ("BBB", "AND"): soporte_bbb, ("BBB", "BRA"): soporte_bbb,
            ("CCC", "AND"): True, ("CCC", "BRA"): True,
        },
        "tecnicos": {"BBB"} if tecnico else set(),
        "min_ton": 10.0,
        "tat_full_h": 1.5,
        "tarifa": {("AAA", "CCC"): 2.0},
        "demanda": {("AAA", "CCC", 1): 50.0},
    }


def leg(pos, origen, destino, salida, llegada):
    return {"pos": pos, "tramo": (origen, destino), "t_dep": salida, "t_arr": llegada}


class AsignacionCargaMultitramosTest(unittest.TestCase):
    def resolver(self, itinerario, datos=None, ttr=2.0):
        return resolver_asignacion(
            itinerario, datos or datos_base(), ttr_h=ttr, tlim=30, gap=0.0, log=False)

    def test_vuelo_directo(self):
        resultado = self.resolver({"A1": [leg(1, "AAA", "CCC", 1.0, 3.0)]})
        self.assertTrue(resultado["ok"])
        self.assertAlmostEqual(resultado["estadisticas"]["toneladas_entregadas"], 50.0, places=5)
        self.assertAlmostEqual(resultado["estadisticas"]["toneladas_directas"], 50.0, places=5)
        self.assertEqual(resultado["estadisticas"]["conexiones_transferencia_usadas"], 0)

    def test_continuacion_through_en_mismo_avion(self):
        itinerario = {
            "A1": [
                leg(1, "AAA", "BBB", 1.0, 2.0),
                leg(2, "BBB", "CCC", 3.2, 4.2),
            ]
        }
        resultado = self.resolver(itinerario)
        self.assertAlmostEqual(resultado["estadisticas"]["toneladas_entregadas"], 50.0, places=5)
        self.assertAlmostEqual(resultado["estadisticas"]["flujo_through_t"], 50.0, places=5)
        self.assertAlmostEqual(resultado["estadisticas"]["flujo_transferido_t"], 0.0, places=5)

    def test_transbordo_entre_operadores(self):
        itinerario = {
            "A1": [leg(1, "AAA", "BBB", 1.0, 2.0)],
            "B1": [leg(1, "BBB", "CCC", 4.0, 5.0)],
        }
        resultado = self.resolver(itinerario, ttr=2.0)
        self.assertAlmostEqual(resultado["estadisticas"]["toneladas_entregadas"], 50.0, places=5)
        self.assertAlmostEqual(resultado["estadisticas"]["flujo_transferido_t"], 50.0, places=5)
        self.assertEqual(resultado["estadisticas"]["transferencias_entre_operadores"], 1)

    def test_rechaza_transbordo_sin_tiempo_suficiente(self):
        itinerario = {
            "A1": [leg(1, "AAA", "BBB", 1.0, 2.0)],
            "B1": [leg(1, "BBB", "CCC", 3.0, 4.0)],
        }
        resultado = self.resolver(itinerario, ttr=2.0)
        self.assertAlmostEqual(resultado["estadisticas"]["toneladas_entregadas"], 0.0, places=5)

    def test_rechaza_transbordo_sin_soporte_comun(self):
        itinerario = {
            "A1": [leg(1, "AAA", "BBB", 1.0, 2.0)],
            "B1": [leg(1, "BBB", "CCC", 4.0, 5.0)],
        }
        resultado = self.resolver(itinerario, datos=datos_base(soporte_bbb=False))
        self.assertAlmostEqual(resultado["estadisticas"]["toneladas_entregadas"], 0.0, places=5)

    def test_escala_tecnica_permite_through_pero_no_transbordo(self):
        through = {
            "A1": [leg(1, "AAA", "BBB", 1.0, 2.0), leg(2, "BBB", "CCC", 3.0, 4.0)]
        }
        resultado_through = self.resolver(through, datos=datos_base(tecnico=True))
        self.assertAlmostEqual(resultado_through["estadisticas"]["toneladas_entregadas"], 50.0, places=5)

        transfer = {
            "A1": [leg(1, "AAA", "BBB", 1.0, 2.0)],
            "B1": [leg(1, "BBB", "CCC", 4.0, 5.0)],
        }
        resultado_transfer = self.resolver(transfer, datos=datos_base(tecnico=True))
        self.assertAlmostEqual(resultado_transfer["estadisticas"]["toneladas_entregadas"], 0.0, places=5)


if __name__ == "__main__":
    unittest.main()
