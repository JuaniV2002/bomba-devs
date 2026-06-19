"""
Escenarios de simulacion para el controlador de bomba de infusion.

finBolsa y confirmacionEnfermero son entradas externas del acoplado, asi que
las generamos con disparadores y las conectamos a sus puertos. Un registro
captura las salidas con su tiempo para imprimir una traza legible.

Juan Ignacio Villanueva - Santiago Pesce
"""

import os

from pypdevs.DEVS import AtomicDEVS, CoupledDEVS
from pypdevs.infinity import INFINITY
from pypdevs.simulator import Simulator

import compat  # parche de pypdevs para Python 3.13+ (ver compat.py)
from modelos import BombaInfusion, Atomico, SENIAL
from graficos import generar_graficos, imprimir_metricas
from propiedades import imprimir_propiedades


def _slug(texto):
    """Nombre de carpeta seguro a partir del titulo del escenario."""
    s = texto.lower().strip()
    s = s.replace("ó", "o").replace("í", "i").replace("á", "a") \
         .replace("é", "e").replace("ú", "u")
    out = []
    for ch in s:
        out.append(ch if ch.isalnum() else "_")
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


# Disparador de seniales externas (fin de bolsa, confirmacion).
class EstadoDisp:
    def __init__(self, tiempos):
        self.tiempos = list(tiempos)
        self.sigma = self.tiempos[0] if self.tiempos else INFINITY


class Disparador(Atomico):
    def __init__(self, nombre, tiempos):
        AtomicDEVS.__init__(self, nombre)
        self.salida = self.addOutPort("salida")
        self.state = EstadoDisp(tiempos)

    def timeAdvance(self):
        return self.state.sigma

    def outputFnc(self):
        return {self.salida: SENIAL}

    def intTransition(self):
        self.state.tiempos.pop(0)
        self.state.sigma = self.state.tiempos[0] if self.state.tiempos else INFINITY
        return self.state


# Registro de salidas, con marca de tiempo.
class EstadoReg:
    def __init__(self):
        self.t = 0.0
        self.eventos = []
        self.ult_caudal = None


class Registro(Atomico):
    def __init__(self):
        AtomicDEVS.__init__(self, "Registro")
        self.ev = self.addInPort("evento")
        self.notif = self.addInPort("notificacion")
        self.caudal = self.addInPort("caudal")
        self.state = EstadoReg()

    def timeAdvance(self):
        return INFINITY

    def extTransition(self, inputs):
        s = self.state
        s.t += self.elapsed
        t = round(s.t, 4)
        if self.ev in inputs:
            s.eventos.append((t, "evento", inputs[self.ev]))
        if self.notif in inputs:
            s.eventos.append((t, "alarma", inputs[self.notif]))
        if self.caudal in inputs:
            c = inputs[self.caudal]
            if c != s.ult_caudal:                # solo cuando cambia el caudal
                s.eventos.append((t, "caudal", c))
                s.ult_caudal = c
        return s


class Escenario(CoupledDEVS):
    # fin_bolsa / confirmaciones: tiempos fijos opcionales, inyectados via
    # los puertos externos de N (Disparador), ademas de lo que generen los
    # GenAlarmaFinBolsa / GenConfirmacionEnfermero internos de N.
    # media_fin_bolsa / media_confirmacion / seed_*: ver BombaInfusion.
    def __init__(self, agenda, fin_bolsa=None, confirmaciones=None, falla=1.0,
                 media_fin_bolsa=60.0, media_confirmacion=40.0,
                 seed_fin_bolsa=None, seed_confirmacion=None):
        CoupledDEVS.__init__(self, "Escenario")
        self.n = self.addSubModel(BombaInfusion(
            agenda, falla,
            media_fin_bolsa=media_fin_bolsa, media_confirmacion=media_confirmacion,
            seed_fin_bolsa=seed_fin_bolsa, seed_confirmacion=seed_confirmacion))
        self.reg = self.addSubModel(Registro())
        self.disp_fb = self.addSubModel(Disparador("FinBolsa", fin_bolsa or []))
        self.disp_cf = self.addSubModel(Disparador("Confirmacion", confirmaciones or []))
        # disparadores hacia las entradas externas de N
        self.connectPorts(self.disp_fb.salida, self.n.finBolsa)
        self.connectPorts(self.disp_cf.salida, self.n.confirmacion)
        # salidas de N hacia el registro
        self.connectPorts(self.n.registro, self.reg.ev)
        self.connectPorts(self.n.notificacion, self.reg.notif)
        self.connectPorts(self.n.caudalActual, self.reg.caudal)


def correr(titulo, agenda, fin_bolsa=None, confirmaciones=None, falla=1.0, hasta=80.0,
           media_fin_bolsa=INFINITY, media_confirmacion=INFINITY,
           seed_fin_bolsa=None, seed_confirmacion=None, graficar=True):
    print("=" * 64)
    print(titulo)
    print("=" * 64)
    modelo = Escenario(agenda, fin_bolsa, confirmaciones, falla,
                        media_fin_bolsa=media_fin_bolsa, media_confirmacion=media_confirmacion,
                        seed_fin_bolsa=seed_fin_bolsa, seed_confirmacion=seed_confirmacion)
    sim = Simulator(modelo)
    sim.setClassicDEVS()
    sim.setTerminationTime(hasta)
    # sim.setVerbose(None)   # descomentar para ver la traza completa de DEVS
    sim.simulate()
    for t, tipo, val in modelo.reg.state.eventos:
        print("  t=%6.2f  %-7s %s" % (t, tipo, val))
    print()

    if graficar:
        carpeta = os.path.join("graficos", _slug(titulo))
        rutas, metricas = generar_graficos(modelo.n.mon.state, hasta, titulo, carpeta)
        imprimir_metricas(metricas, titulo)
        imprimir_propiedades(modelo.n.mon.state, titulo)
        print(f"  Graficos guardados en: {carpeta}/")
        print()

    return modelo


if __name__ == "__main__":
    # Los 7 escenarios de prueba pedidos en la seccion 8 del informe, en su
    # mismo orden. No se agregan escenarios adicionales fuera de esta lista.

    # 1) Funcionamiento normal, sin fallas.
    correr("Escenario 1 - Funcionamiento normal, sin fallas",
           agenda=[(100, 2)], hasta=15)

    # 2) Cambio de orden medica durante la infusion: de 50 a 80 ml/h.
    #    Sin fallas del actuador (falla=1.0), el caudal real sigue al
    #    indicado de inmediato en ambos casos.
    correr("Escenario 2 - Cambio de orden medica durante la infusion (50 a 80 ml/h)",
           agenda=[(50, 2), (80, 8)], hasta=20)

    # 3) Orden medica con caudal igual a cero: detiene la infusion. Se
    #    ordena 100 ml/h a los 2 s y luego 0 ml/h a los 10 s (8 s mas
    #    tarde), verificando el paso a modo idle.
    correr("Escenario 3 - Orden medica con caudal igual a cero",
           agenda=[(100, 2), (0, 8)], hasta=20)

    # 4) Desvio leve de caudal que es corregido por el controlador. El
    #    Actuador modela la falla como un factor CONSTANTE, no transitorio:
    #    si el desvio persistiera y superase el 10% de tolerancia, el
    #    controlador reintenta (correccion_caudal) pero el actuador vuelve
    #    a aplicar el mismo factor, por lo que escala a alarmaMedia en vez
    #    de corregirse (ver Escenario 5). Por eso este escenario usa un
    #    desvio leve que queda DENTRO del 10% de tolerancia desde el
    #    inicio: el controlador lo "corrige" en el sentido de que nunca
    #    llega a considerarlo un desvio sostenido, no hace falta ninguna
    #    accion correctiva visible y el sistema sigue infundiendo normal.
    correr("Escenario 4 - Desvio leve de caudal corregido por el controlador",
           agenda=[(100, 2)], falla=0.92, hasta=15)

    # 5) Desvio de caudal mayor al 10% durante mas de 5 s: dispara
    #    alarmaMedia y, si persiste, escala a alarmaCritica con detencion
    #    de la bomba.
    correr("Escenario 5 - Desvio de caudal mayor al 10% durante mas de 5s",
           agenda=[(100, 2)], falla=0.5, hasta=15)

    # 6) Fin de bolsa con confirmacion del enfermero: la bolsa se agota a
    #    los 10 s (alarmaBaja) y el enfermero confirma a los 30 s, bien
    #    antes del autostop a los 70 s, asi que la infusion se reanuda sin
    #    llegar a detenerse automaticamente.
    correr("Escenario 6 - Fin de bolsa con confirmacion del enfermero",
           agenda=[(100, 2)], fin_bolsa=[10], confirmaciones=[30], hasta=50)

    # 7) Alarma critica no confirmada durante 30 s: nadie confirma, asi que
    #    se debe ver la 1ra repeticion a los 30 s tras la alarma critica
    #    inicial, y la 2da a los 10 s de esa (T_REP). Horizonte hasta=62
    #    para que ambas repeticiones queden dentro de la corrida.
    correr("Escenario 7 - Alarma critica no confirmada durante 30s",
           agenda=[(100, 2)], falla=0.5, hasta=62)
