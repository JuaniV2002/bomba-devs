"""
Controlador de bomba de infusion - implementacion DEVS.
Version reducida del proyecto, segun la especificacion del informe.

Simulador: PythonPDEVS (kernel normal, DEVS clasico).
Juan Ignacio Villanueva - Santiago Pesce
Simulacion de Sistemas - UNRC
"""

from pypdevs.DEVS import AtomicDEVS, CoupledDEVS
from pypdevs.infinity import INFINITY

# Parametros del sistema (Cuadro 1 del informe)
MAX_CAUDAL = 200      # ml/h
T_MUESTREO = 1        # periodo de muestreo del sensor (s)
T_LATENCIA = 0.5      # latencia del actuador (s)
TOL        = 0.10     # desvio maximo tolerado (10%)
N_MEDIA    = 6        # muestras consecutivas con desvio -> alarmaMedia
N_CRITICA  = 9        # N_MEDIA + 3 -> alarmaCritica (3 s despues)
T_BOLSA    = 60       # ventana maxima tras fin de bolsa (s)
T_CONF     = 30       # espera de confirmacion de una alarma critica (s)
T_REP      = 10       # periodo de repeticion de la alarma critica (s)

# Token para los eventos que en la spec son del tipo {signal} o {stop}.
# El valor no importa, solo importa que el evento llegue al puerto.
SENIAL = 1


# El solver de DEVS clasico ordena la lista de modelos inminentes cuando hay
# mas de uno a la vez (sensor + generador, etc.). En Python 3 los objetos no
# son comparables por defecto, asi que les damos un orden por nombre.
class Atomico(AtomicDEVS):
    def __lt__(self, other):
        return self.getModelFullName() < other.getModelFullName()


def desviado(real, obj):
    return abs(real - obj) > TOL * obj


# ---------------------------------------------------------------------------
# 1. Generador de ordenes medicas
# ---------------------------------------------------------------------------
class EstadoGen:
    def __init__(self, agenda):
        # agenda: lista de (caudal, espera_hasta_emitir)
        self.agenda = list(agenda)
        self.sigma = self.agenda[0][1] if self.agenda else INFINITY


class GenOrdenes(Atomico):
    def __init__(self, agenda):
        AtomicDEVS.__init__(self, "GenOrdenes")
        self.ordenMedica = self.addOutPort("ordenMedica")
        self.state = EstadoGen(agenda)

    def timeAdvance(self):
        return self.state.sigma

    def outputFnc(self):
        return {self.ordenMedica: self.state.agenda[0][0]}

    def intTransition(self):
        self.state.agenda.pop(0)
        if self.state.agenda:
            self.state.sigma = self.state.agenda[0][1]
        else:
            self.state.sigma = INFINITY
        return self.state


# ---------------------------------------------------------------------------
# 2. Sensor de flujo
# ---------------------------------------------------------------------------
class EstadoSensor:
    def __init__(self):
        self.caudal = 0.0
        self.sigma = T_MUESTREO


class SensorFlujo(Atomico):
    def __init__(self):
        AtomicDEVS.__init__(self, "SensorFlujo")
        self.caudalActual = self.addInPort("caudalActual")
        self.sensorFlujo = self.addOutPort("sensorFlujo")
        self.state = EstadoSensor()

    def timeAdvance(self):
        return self.state.sigma

    def outputFnc(self):
        return {self.sensorFlujo: self.state.caudal}

    def intTransition(self):
        self.state.sigma = T_MUESTREO          # rearma el periodo completo
        return self.state

    def extTransition(self, inputs):
        self.state.caudal = inputs[self.caudalActual]
        self.state.sigma -= self.elapsed       # no reinicia, solo descuenta
        return self.state


# ---------------------------------------------------------------------------
# 3. Actuador de la bomba
# ---------------------------------------------------------------------------
class EstadoActuador:
    def __init__(self):
        self.objetivo = 0.0
        self.sigma = INFINITY


class Actuador(Atomico):
    # 'falla' es un factor para inyectar desvios en los escenarios
    # (1.0 = sin falla, entrega el caudal exacto como en la spec).
    def __init__(self, falla=1.0):
        AtomicDEVS.__init__(self, "Actuador")
        self.ajustar = self.addInPort("ajustarCaudal")
        self.detener = self.addInPort("detenerBomba")
        self.caudalActual = self.addOutPort("caudalActual")
        self.falla = falla
        self.state = EstadoActuador()

    def timeAdvance(self):
        return self.state.sigma

    def outputFnc(self):
        return {self.caudalActual: self.state.objetivo * self.falla}

    def intTransition(self):
        self.state.sigma = INFINITY            # aplicado, pasivo hasta nueva orden
        return self.state

    def extTransition(self, inputs):
        if self.ajustar in inputs:
            self.state.objetivo = inputs[self.ajustar]
            self.state.sigma = T_LATENCIA
        elif self.detener in inputs:
            self.state.objetivo = 0.0
            self.state.sigma = T_LATENCIA
        return self.state


# ---------------------------------------------------------------------------
# 4. Controlador de bomba
# ---------------------------------------------------------------------------
class EstadoCtrl:
    def __init__(self):
        self.modo = "idle"
        self.objetivo = 0.0
        self.medido = 0.0
        self.desvios = 0
        self.cmd = "nada"
        self.sigma = INFINITY


class Controlador(Atomico):
    def __init__(self):
        AtomicDEVS.__init__(self, "Controlador")
        # entradas
        self.ordenMedica = self.addInPort("ordenMedica")
        self.sensorFlujo = self.addInPort("sensorFlujo")
        self.finBolsa = self.addInPort("finBolsa")
        self.confirmacion = self.addInPort("confirmacionEnfermero")
        # salidas
        self.ajustar = self.addOutPort("ajustarCaudal")
        self.detener = self.addOutPort("detenerBomba")
        self.alarmaBaja = self.addOutPort("alarmaBaja")
        self.alarmaMedia = self.addOutPort("alarmaMedia")
        self.alarmaCritica = self.addOutPort("alarmaCritica")
        self.registro = self.addOutPort("registrarEvento")
        self.state = EstadoCtrl()

    def timeAdvance(self):
        return self.state.sigma

    def extTransition(self, inputs):
        s = self.state
        if self.ordenMedica in inputs:
            v = inputs[self.ordenMedica]
            if v > 0:
                s.modo, s.objetivo, s.desvios, s.cmd, s.sigma = "infund", v, 0, "iniciar", 0
            else:
                s.modo, s.objetivo, s.desvios, s.cmd, s.sigma = "idle", 0.0, 0, "parar", 0
        elif self.finBolsa in inputs and s.modo == "infund":
            s.modo, s.cmd, s.sigma = "bolsa", "baja", 0
        elif self.confirmacion in inputs and s.modo == "detenida":
            s.modo, s.objetivo, s.desvios, s.cmd, s.sigma = "idle", 0.0, 0, "confirmar", 0
        elif self.confirmacion in inputs:
            pass  # confirmacion irrelevante en otros modos: se ignora sin tocar sigma
        elif self.sensorFlujo in inputs and s.modo == "infund":
            v = inputs[self.sensorFlujo]
            s.medido = v
            if desviado(v, s.objetivo):
                s.desvios += 1
                n = s.desvios
                if n >= N_CRITICA:
                    s.modo, s.cmd, s.sigma = "detenida", "critica", 0
                elif n == N_MEDIA:
                    s.cmd, s.sigma = "media", 0
                elif n > N_MEDIA:
                    s.cmd, s.sigma = "nada", INFINITY
                else:
                    s.cmd, s.sigma = "corregir", 0
            else:
                s.desvios, s.cmd, s.sigma = 0, "nada", INFINITY
        elif self.sensorFlujo in inputs and s.modo == "bolsa":
            s.medido = inputs[self.sensorFlujo]
            s.sigma = max(0.0, s.sigma - self.elapsed)  # conserva cuenta regresiva, nunca negativo
        else:
            s.sigma = max(0.0, s.sigma - self.elapsed)  # evento irrelevante: descuenta sin ir a negativo
        return s

    def intTransition(self):
        s = self.state
        c = s.cmd
        if c == "iniciar":
            s.modo, s.cmd, s.sigma = "infund", "nada", INFINITY
        elif c == "parar":
            s.modo, s.objetivo, s.desvios, s.cmd, s.sigma = "idle", 0.0, 0, "nada", INFINITY
        elif c == "corregir":
            s.modo, s.cmd, s.sigma = "infund", "nada", INFINITY
        elif c == "media":
            s.modo, s.cmd, s.sigma = "infund", "nada", INFINITY
        elif c == "critica":
            s.modo, s.desvios, s.cmd, s.sigma = "detenida", 0, "nada", INFINITY
        elif c == "baja":
            s.modo, s.cmd, s.sigma = "bolsa", "autostop", T_BOLSA
        elif c == "autostop":
            s.modo, s.objetivo, s.desvios, s.cmd, s.sigma = "idle", 0.0, 0, "nada", INFINITY
        elif c == "confirmar":
            s.modo, s.objetivo, s.desvios, s.cmd, s.sigma = "idle", 0.0, 0, "nada", INFINITY
        else:
            s.cmd, s.sigma = "nada", INFINITY
        return s

    def outputFnc(self):
        s = self.state
        c = s.cmd
        if c == "iniciar":
            return {self.ajustar: s.objetivo, self.registro: "inicio_infusion"}
        if c == "parar":
            return {self.detener: SENIAL, self.registro: "stop_por_orden"}
        if c == "corregir":
            return {self.ajustar: s.objetivo, self.registro: "correccion_caudal"}
        if c == "media":
            return {self.alarmaMedia: SENIAL, self.registro: "desvio_sostenido"}
        if c == "critica":
            return {self.alarmaCritica: SENIAL, self.detener: SENIAL, self.registro: "critica_stop"}
        if c == "baja":
            return {self.alarmaBaja: SENIAL, self.registro: "fin_bolsa"}
        if c == "autostop":
            return {self.detener: SENIAL, self.registro: "autostop_fin_bolsa"}
        if c == "confirmar":
            return {self.registro: "confirmacion_enfermero"}
        return {}


# ---------------------------------------------------------------------------
# 5. Modulo de alarmas
# ---------------------------------------------------------------------------
class EstadoAlarmas:
    def __init__(self):
        self.modo = "ocioso"
        self.pend = None
        self.sigma = INFINITY


class ModuloAlarmas(Atomico):
    def __init__(self):
        AtomicDEVS.__init__(self, "ModuloAlarmas")
        self.alarmaBaja = self.addInPort("alarmaBaja")
        self.alarmaMedia = self.addInPort("alarmaMedia")
        self.alarmaCritica = self.addInPort("alarmaCritica")
        self.confirmacion = self.addInPort("confirmacionEnfermero")
        self.notificacion = self.addOutPort("notificacionAlarma")
        self.state = EstadoAlarmas()

    def timeAdvance(self):
        return self.state.sigma

    def outputFnc(self):
        if self.state.pend is not None:
            return {self.notificacion: self.state.pend}
        return {}

    def extTransition(self, inputs):
        s = self.state
        if self.alarmaBaja in inputs:
            s.modo, s.pend, s.sigma = "ocioso", "baja", 0
        elif self.alarmaMedia in inputs:
            s.modo, s.pend, s.sigma = "ocioso", "media", 0
        elif self.alarmaCritica in inputs and s.modo == "ocioso":
            s.modo, s.pend, s.sigma = "critInic", "critica", 0
        elif self.confirmacion in inputs:
            # Si hay una transicion interna inminente (sigma=0), la dejamos ejecutar
            # primero (PyPDEVS da prioridad a intTransition): solo cancelamos si no
            # hay salida pendiente inmediata, para evitar emitir la notificacion
            # critica y luego silenciarla en el mismo instante de tiempo.
            if s.sigma > 0:
                s.modo, s.pend, s.sigma = "ocioso", None, INFINITY
            else:
                # sigma=0: la transicion interna ya esta programada para este instante;
                # la dejamos correr. El modulo quedara en esperaConf/repitiendo, y la
                # siguiente confirmacion que llegue (o la que reenvia el controlador)
                # lo silenciara en el proximo paso.
                s.sigma = 0
        else:
            s.sigma = max(0.0, s.sigma - self.elapsed)
        return s

    def intTransition(self):
        s = self.state
        if s.modo == "ocioso":
            s.pend, s.sigma = None, INFINITY
        elif s.modo == "critInic":
            s.modo, s.pend, s.sigma = "esperaConf", "critica", T_CONF
        elif s.modo == "esperaConf":
            s.modo, s.pend, s.sigma = "repitiendo", "critica", T_REP
        elif s.modo == "repitiendo":
            s.modo, s.pend, s.sigma = "repitiendo", "critica", T_REP
        return s


# ---------------------------------------------------------------------------
# Modelo acoplado N
# ---------------------------------------------------------------------------
class BombaInfusion(CoupledDEVS):
    # finBolsa y confirmacionEnfermero son entradas externas del acoplado.
    def __init__(self, agenda, falla=1.0):
        CoupledDEVS.__init__(self, "BombaInfusion")
        # puertos externos
        self.finBolsa = self.addInPort("finBolsa")
        self.confirmacion = self.addInPort("confirmacionEnfermero")
        self.registro = self.addOutPort("registrarEvento")
        self.notificacion = self.addOutPort("notificacionAlarma")
        self.sensorFlujo = self.addOutPort("sensorFlujo")
        self.caudalActual = self.addOutPort("caudalActual")
        # componentes
        self.gen = self.addSubModel(GenOrdenes(agenda))
        self.sen = self.addSubModel(SensorFlujo())
        self.ctrl = self.addSubModel(Controlador())
        self.act = self.addSubModel(Actuador(falla))
        self.alm = self.addSubModel(ModuloAlarmas())
        # acoplamientos internos (IC)
        self.connectPorts(self.gen.ordenMedica, self.ctrl.ordenMedica)
        self.connectPorts(self.sen.sensorFlujo, self.ctrl.sensorFlujo)
        self.connectPorts(self.ctrl.ajustar, self.act.ajustar)
        self.connectPorts(self.ctrl.detener, self.act.detener)
        self.connectPorts(self.act.caudalActual, self.sen.caudalActual)
        self.connectPorts(self.ctrl.alarmaBaja, self.alm.alarmaBaja)
        self.connectPorts(self.ctrl.alarmaMedia, self.alm.alarmaMedia)
        self.connectPorts(self.ctrl.alarmaCritica, self.alm.alarmaCritica)
        # entradas externas (EIC)
        self.connectPorts(self.finBolsa, self.ctrl.finBolsa)
        self.connectPorts(self.confirmacion, self.ctrl.confirmacion)
        self.connectPorts(self.confirmacion, self.alm.confirmacion)
        # salidas externas (EOC)
        self.connectPorts(self.ctrl.registro, self.registro)
        self.connectPorts(self.alm.notificacion, self.notificacion)
        self.connectPorts(self.sen.sensorFlujo, self.sensorFlujo)
        self.connectPorts(self.act.caudalActual, self.caudalActual)

    def select(self, imm):
        # Prioridad ante empates: Ctrl, Act, Sen, Alm, G
        for m in (self.ctrl, self.act, self.sen, self.alm, self.gen):
            if m in imm:
                return m
        return imm[0]
