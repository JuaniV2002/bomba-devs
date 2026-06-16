"""
Escenarios de simulacion para el controlador de bomba de infusion.

finBolsa y confirmacionEnfermero son entradas externas del acoplado, asi que
las generamos con disparadores y las conectamos a sus puertos. Un registro
captura las salidas con su tiempo para imprimir una traza legible.

Juan Ignacio Villanueva - Santiago Pesce
"""

from pypdevs.DEVS import AtomicDEVS, CoupledDEVS
from pypdevs.infinity import INFINITY
from pypdevs.simulator import Simulator

import compat  # parche de pypdevs para Python 3.13+ (ver compat.py)
from modelos import BombaInfusion, Atomico, SENIAL


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
    def __init__(self, agenda, fin_bolsa=None, confirmaciones=None, falla=1.0):
        CoupledDEVS.__init__(self, "Escenario")
        self.n = self.addSubModel(BombaInfusion(agenda, falla))
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


def correr(titulo, agenda, fin_bolsa=None, confirmaciones=None, falla=1.0, hasta=80.0):
    print("=" * 64)
    print(titulo)
    print("=" * 64)
    modelo = Escenario(agenda, fin_bolsa, confirmaciones, falla)
    sim = Simulator(modelo)
    sim.setClassicDEVS()
    sim.setTerminationTime(hasta)
    # sim.setVerbose(None)   # descomentar para ver la traza completa de DEVS
    sim.simulate()
    for t, tipo, val in modelo.reg.state.eventos:
        print("  t=%6.2f  %-7s %s" % (t, tipo, val))
    print()


if __name__ == "__main__":
    # 1) Operacion normal: una orden de 100 ml/h a los 2 s, sin fallas.
    correr("Escenario 1 - Operacion normal",
           agenda=[(100, 2)], hasta=15)

    # 2) Desvio sostenido: el actuador entrega la mitad del caudal ordenado.
    #    Esperamos alarmaMedia, escalamiento a critica y bomba detenida. La
    #    alarma critica se repite (a los 30 s y luego cada 10 s) hasta que el
    #    enfermero confirma a los 55 s, lo que ademas libera el bloqueo.
    #    Se usa hasta=70 para dejar margen tras la confirmacion (a los 55 s)
    #    y verificar que no se emiten mas repeticiones despues de ella.
    correr("Escenario 2 - Desvio sostenido y alarma critica",
           agenda=[(100, 2)], confirmaciones=[55], falla=0.5, hasta=70)

    # 3) Fin de bolsa: a los 10 s se agota la bolsa. Esperamos alarmaBaja y,
    #    sin intervencion, el autostop 60 s despues.
    correr("Escenario 3 - Fin de bolsa",
           agenda=[(100, 2)], fin_bolsa=[10], hasta=75)
