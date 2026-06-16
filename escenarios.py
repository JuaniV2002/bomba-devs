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


def graficar(titulo, eventos, hasta, objetivo=None, archivo=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # Reconstruir la señal de caudal como función escalón
    tc, vc = [0.0], [0.0]
    for t, tp, v in eventos:
        if tp == "caudal":
            tc.append(t)
            vc.append(v)
    tc.append(hasta)
    vc.append(vc[-1])

    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.step(tc, vc, where='post', color='steelblue', lw=2, label='caudal real (ml/h)')
    ax.fill_between(tc, vc, step='post', alpha=0.12, color='steelblue')
    if objetivo is not None:
        ax.axhline(objetivo, color='dimgray', ls='--', lw=1, alpha=0.55,
                   label=f'objetivo: {objetivo} ml/h')

    ymax = max(vc) if max(vc) > 0 else 100
    ax.set_ylim(-8, ymax * 1.65)

    t_eventos_set = {t for t, tp, _ in eventos if tp == "evento"}

    EVENTO_ESTILO = {
        'inicio_infusion':        ('seagreen',     '-',  'inicio'),
        'desvio_sostenido':       ('darkorange',   '--', 'alarmaMedia'),
        'critica_stop':           ('crimson',      '-',  'alarmaCritica + stop'),
        'fin_bolsa':              ('mediumpurple', '--', 'finBolsa / alarmaBaja'),
        'autostop_fin_bolsa':     ('crimson',      '-',  'autostop'),
        'confirmacion_enfermero': ('seagreen',     '-',  'confirmación'),
    }

    def poner(t, color, ls, lw, etiqueta, alpha=0.9):
        ax.axvline(t, color=color, ls=ls, lw=lw, alpha=alpha, zorder=3)
        ax.text(t + hasta * 0.006, ymax * 1.58, etiqueta,
                color=color, fontsize=7, rotation=90, va='top', ha='left',
                bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='none', alpha=0.8))

    for t, tp, val in eventos:
        if tp == "evento" and val in EVENTO_ESTILO:
            color, ls, etiqueta = EVENTO_ESTILO[val]
            poner(t, color, ls, 1.5, etiqueta)
        elif tp == "alarma":
            if val == "critica" and t not in t_eventos_set:
                poner(t, 'crimson', '--', 1.0, 'rep. alarmaCritica', alpha=0.65)
            elif val == "baja" and t not in t_eventos_set:
                poner(t, 'mediumpurple', '--', 1.0, 'alarmaBaja', alpha=0.65)

    ax.set_xlabel('Tiempo (s)', fontsize=9)
    ax.set_ylabel('Caudal (ml/h)', fontsize=9)
    ax.set_title(titulo, fontsize=10, fontweight='bold')
    ax.set_xlim(-0.5, hasta)
    ax.legend(loc='upper right', fontsize=8, framealpha=0.9)
    ax.grid(True, alpha=0.2)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    if archivo:
        fig.savefig(archivo, dpi=150, bbox_inches='tight')
    plt.close(fig)


def correr(titulo, agenda, fin_bolsa=None, confirmaciones=None, falla=1.0, hasta=80.0,
           archivo=None, objetivo=None):
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
    if archivo:
        graficar(titulo, modelo.reg.state.eventos, hasta, objetivo=objetivo, archivo=archivo)
        print(f"  → figura guardada en {archivo}\n")


if __name__ == "__main__":
    # 1) Operacion normal: una orden de 100 ml/h a los 2 s, sin fallas.
    correr("Escenario 1 - Operacion normal",
           agenda=[(100, 2)], hasta=15,
           archivo="esc1.pdf")

    # 2) Desvio sostenido: el actuador entrega la mitad del caudal ordenado.
    #    Esperamos alarmaMedia, escalamiento a critica y bomba detenida. La
    #    alarma critica se repite (a los 30 s y luego cada 10 s) hasta que el
    #    enfermero confirma a los 55 s, lo que ademas libera el bloqueo.
    #    Se usa hasta=70 para dejar margen tras la confirmacion (a los 55 s)
    #    y verificar que no se emiten mas repeticiones despues de ella.
    correr("Escenario 2 - Desvio sostenido y alarma critica",
           agenda=[(100, 2)], confirmaciones=[55], falla=0.5, hasta=70,
           archivo="esc2.pdf", objetivo=100)

    # 3) Fin de bolsa: a los 10 s se agota la bolsa. Esperamos alarmaBaja y,
    #    sin intervencion, el autostop 60 s despues.
    correr("Escenario 3 - Fin de bolsa",
           agenda=[(100, 2)], fin_bolsa=[10], hasta=75,
           archivo="esc3.pdf")
