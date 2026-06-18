"""
Controlador de bomba de infusion - implementacion DEVS.
Version reducida del proyecto, segun la especificacion del informe.

Simulador: PythonPDEVS (kernel normal, DEVS clasico).
Juan Ignacio Villanueva - Santiago Pesce
Simulacion de Sistemas - UNRC
"""

import random

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
# 2. Generador de alarma de fin de bolsa
# ---------------------------------------------------------------------------
# Modela la llegada del evento {finBolsa}: la bolsa de solucion se agota en
# un instante aleatorio, exponencial con media 'media' (en segundos) desde
# el inicio de la simulacion. Se dispara una sola vez por corrida, como
# corresponde a una sola bolsa.
class EstadoGenFinBolsa:
    def __init__(self, media, seed=None):
        self.media = media
        self.rng = random.Random(seed)
        self.disparado = False
        self.sigma = self.rng.expovariate(1.0 / media) if 0 < media < INFINITY else INFINITY


class GenAlarmaFinBolsa(Atomico):
    def __init__(self, media, seed=None):
        AtomicDEVS.__init__(self, "GenAlarmaFinBolsa")
        self.finBolsa = self.addOutPort("finBolsa")
        self.state = EstadoGenFinBolsa(media, seed)

    def timeAdvance(self):
        return self.state.sigma

    def outputFnc(self):
        return {self.finBolsa: SENIAL}

    def intTransition(self):
        # una sola bolsa por corrida: tras emitir, queda pasivo para siempre
        self.state.disparado = True
        self.state.sigma = INFINITY
        return self.state


# ---------------------------------------------------------------------------
# 3. Generador de confirmacion del enfermero
# ---------------------------------------------------------------------------
# Modela al enfermero que revisa la bomba y confirma una alarma critica en
# instantes aleatorios. Cada confirmacion se reprograma con una nueva
# muestra exponencial de media 'media' (en segundos), simulando rondas de
# control periodicas pero no deterministicas. El evento {confirmacionEnfermero}
# solo tiene efecto en el sistema cuando hay una alarma critica activa; si no
# la hay, el Controlador y el ModuloAlarmas simplemente lo ignoran.
class EstadoGenConfirmacion:
    def __init__(self, media, seed=None):
        self.media = media
        self.rng = random.Random(seed)
        self.sigma = self.rng.expovariate(1.0 / media) if 0 < media < INFINITY else INFINITY


class GenConfirmacionEnfermero(Atomico):
    def __init__(self, media, seed=None):
        AtomicDEVS.__init__(self, "GenConfirmacionEnfermero")
        self.confirmacion = self.addOutPort("confirmacionEnfermero")
        self.state = EstadoGenConfirmacion(media, seed)

    def timeAdvance(self):
        return self.state.sigma

    def outputFnc(self):
        return {self.confirmacion: SENIAL}

    def intTransition(self):
        # se reprograma: el enfermero sigue haciendo rondas durante la corrida
        m = self.state.media
        self.state.sigma = self.state.rng.expovariate(1.0 / m) if 0 < m < INFINITY else INFINITY
        return self.state


# ---------------------------------------------------------------------------
# 4. Sensor de flujo
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
# 5. Actuador de la bomba
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
# 6. Controlador de bomba
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
# 7. Modulo de alarmas
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
# 8. Monitor de simulacion
# ---------------------------------------------------------------------------
# Observa las salidas relevantes del sistema (sin participar de la logica de
# control) y construye las series y metricas que pide la seccion 11 del
# informe ("Resultados esperados"):
#   - caudal indicado vs caudal real
#   - estado de la bomba a lo largo del tiempo
#   - registro de alarmas generadas
#   - tiempo de respuesta ante desvios de caudal
#   - tiempo de respuesta ante fin de bolsa
#   - cantidad de detenciones preventivas
#   - porcentaje de tiempo con infusion correcta
#
# El modo de la bomba no se expone como puerto propio del Controlador (para
# no tocar su logica), asi que el Monitor lo reconstruye de forma
# deterministica a partir de la secuencia de strings que ya emite
# registrarEvento. Cada string mapea 1 a 1 con una transicion de modo
# conocida (ver Controlador.outputFnc/intTransition).
EVENTO_A_MODO = {
    "inicio_infusion":    "infund",
    "stop_por_orden":     "idle",
    "correccion_caudal":  "infund",
    "desvio_sostenido":   "infund",   # alarmaMedia: sigue infundiendo
    "critica_stop":       "detenida",
    "fin_bolsa":          "bolsa",
    "autostop_fin_bolsa": "idle",
    "confirmacion_enfermero": "idle",
}

# Eventos que representan una detencion preventiva de la bomba (no por una
# orden medica con caudal 0, que es una parada solicitada, no preventiva).
EVENTOS_DETENCION_PREVENTIVA = {"critica_stop", "autostop_fin_bolsa"}


class EstadoMonitor:
    def __init__(self):
        self.t = 0.0
        # series temporales: lista de (t, valor)
        self.serie_indicado = []     # caudal objetivo (ajustarCaudal)
        self.serie_real = []         # caudal real (caudalActual)
        self.serie_modo = [(0.0, "idle")]  # estado de la bomba (escalones)
        self.alarmas = []            # (t, tipo) - "baja"/"media"/"critica"
        self.eventos = []            # (t, nombre) - traza cruda de registrarEvento
        # metricas derivadas, calculadas incrementalmente
        self.modo_actual = "idle"
        self.objetivo_actual = 0.0
        # t_inicio_desvio: comienzo de la racha de desvio actualmente activa
        # (fuera de tolerancia). Se cierra con la 1ra muestra que vuelve a
        # tolerancia (ver sensorFlujo abajo) y alimenta tramos_desvio, que
        # se usa para "% de tiempo con infusion correcta".
        self.t_inicio_desvio = None
        self.tramos_desvio = []          # [] de (t_inicio, t_fin) corregidos
        # t_resp_pendiente: igual que t_inicio_desvio pero se cierra con la
        # primera alarmaMedia/alarmaCritica (no con el sensor), para medir
        # el "tiempo de respuesta ante desvios de caudal" que pide el
        # enunciado. Es independiente de t_inicio_desvio porque ambos se
        # cierran con eventos distintos.
        self.t_resp_pendiente = None
        self.tiempos_resp_desvio = []    # [] de (t_alarma - t_resp_pendiente)
        self.t_fin_bolsa = None          # t en que se detecto finBolsa
        self.tiempos_resp_finbolsa = []  # [] de (t_detencion - t_fin_bolsa)
        self.detenciones_preventivas = 0

    def cerrar(self, t_final):
        """Cierra tramos/series abiertos al terminar la simulacion. Llamar
        una sola vez, despues de sim.simulate(), con el tiempo final de la
        corrida (el valor pasado a setTerminationTime)."""
        if self.t_inicio_desvio is not None:
            self.tramos_desvio.append((self.t_inicio_desvio, t_final))
            self.t_inicio_desvio = None
        self.serie_modo.append((t_final, self.modo_actual))  # cierra el ultimo escalon

    def tiempo_infusion_correcta_pct(self, t_final):
        """% del tiempo total en que el sistema estuvo en modo 'infund' y
        dentro de tolerancia (osea, sin un tramo de desvio activo)."""
        if t_final <= 0:
            return 0.0
        t_infund = 0.0
        modos = self.serie_modo  # ya debe estar cerrada (ver cerrar())
        for (t0, modo), (t1, _) in zip(modos, modos[1:]):
            if modo == "infund":
                t_infund += (t1 - t0)
        t_desviado = sum(fin - ini for ini, fin in self.tramos_desvio)
        t_correcta = max(0.0, t_infund - t_desviado)
        return 100.0 * t_correcta / t_final


class Monitor(Atomico):
    # ajustar: caudal indicado (Controlador.ajustarCaudal)
    # caudalActual: caudal real (Actuador.caudalActual)
    # registro: traza de eventos (Controlador.registrarEvento)
    # notificacion: alarmas (ModuloAlarmas.notificacionAlarma)
    # sensorFlujo: muestra periodica del caudal real, usada solo para
    #              inferir cuando un desvio corregido vuelve a tolerancia
    #              (ese caso no emite ningun evento por registrarEvento).
    def __init__(self):
        AtomicDEVS.__init__(self, "Monitor")
        self.ajustar = self.addInPort("ajustarCaudal")
        self.caudalActual = self.addInPort("caudalActual")
        self.registro = self.addInPort("registrarEvento")
        self.notificacion = self.addInPort("notificacionAlarma")
        self.sensorFlujo = self.addInPort("sensorFlujo")
        self.state = EstadoMonitor()

    def timeAdvance(self):
        return INFINITY  # el Monitor nunca emite, solo observa

    def extTransition(self, inputs):
        s = self.state
        s.t += self.elapsed
        t = round(s.t, 4)

        if self.ajustar in inputs:
            s.objetivo_actual = inputs[self.ajustar]
            s.serie_indicado.append((t, s.objetivo_actual))

        if self.caudalActual in inputs:
            s.serie_real.append((t, inputs[self.caudalActual]))

        if self.sensorFlujo in inputs and s.modo_actual == "infund":
            medido = inputs[self.sensorFlujo]
            fuera_tol = desviado(medido, s.objetivo_actual) if s.objetivo_actual > 0 else False
            if fuera_tol and s.t_inicio_desvio is None:
                s.t_inicio_desvio = t
            elif not fuera_tol and s.t_inicio_desvio is not None:
                s.tramos_desvio.append((s.t_inicio_desvio, t))
                s.t_inicio_desvio = None

        if self.registro in inputs:
            ev = inputs[self.registro]
            s.eventos.append((t, ev))

            # apertura del cronometro de "tiempo de respuesta ante desvio":
            # la primera correccion_caudal de una racha marca el comienzo.
            # Si ya habia uno pendiente (correcciones consecutivas), no se
            # reinicia.
            if ev == "correccion_caudal" and s.t_resp_pendiente is None:
                s.t_resp_pendiente = t
            elif ev in ("inicio_infusion", "stop_por_orden"):
                # nueva orden: cualquier cronometro de desvio en curso queda
                # obsoleto, ya que el objetivo cambio.
                s.t_resp_pendiente = None

            if ev == "fin_bolsa":
                s.t_fin_bolsa = t
            if ev in EVENTOS_DETENCION_PREVENTIVA:
                s.detenciones_preventivas += 1
                if ev == "autostop_fin_bolsa" and s.t_fin_bolsa is not None:
                    s.tiempos_resp_finbolsa.append(t - s.t_fin_bolsa)
                    s.t_fin_bolsa = None

            nuevo_modo = EVENTO_A_MODO.get(ev)
            if nuevo_modo is not None and nuevo_modo != s.modo_actual:
                s.modo_actual = nuevo_modo
                s.serie_modo.append((t, nuevo_modo))

        if self.notificacion in inputs:
            tipo = inputs[self.notificacion]
            s.alarmas.append((t, tipo))
            # cierre del tiempo de respuesta ante desvio: la primera vez que
            # se emite alarmaMedia o alarmaCritica tras la 1ra correccion de
            # una racha. Se cierra una sola vez por racha (de ahi el reset
            # incondicional de t_resp_pendiente).
            if tipo in ("media", "critica") and s.t_resp_pendiente is not None:
                s.tiempos_resp_desvio.append(t - s.t_resp_pendiente)
                s.t_resp_pendiente = None

        return s


# ---------------------------------------------------------------------------
# Modelo acoplado N
# ---------------------------------------------------------------------------
class BombaInfusion(CoupledDEVS):
    # finBolsa y confirmacionEnfermero son entradas externas del acoplado,
    # ademas de generarse internamente: el modelo trae sus propios
    # GenAlarmaFinBolsa / GenConfirmacionEnfermero (signal aleatoria,
    # exponencial), pero los puertos externos se conservan para poder
    # forzar un evento puntual desde un escenario (por ej. un Disparador
    # con tiempos fijos), igual que se hacia antes de tener generadores.
    # media_fin_bolsa / media_confirmacion: media (s) de las exponenciales
    # de los generadores internos. seed_* permite reproducibilidad.
    def __init__(self, agenda, falla=1.0,
                 media_fin_bolsa=60.0, media_confirmacion=40.0,
                 seed_fin_bolsa=None, seed_confirmacion=None):
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
        self.genFB = self.addSubModel(GenAlarmaFinBolsa(media_fin_bolsa, seed_fin_bolsa))
        self.genCF = self.addSubModel(GenConfirmacionEnfermero(media_confirmacion, seed_confirmacion))
        self.sen = self.addSubModel(SensorFlujo())
        self.ctrl = self.addSubModel(Controlador())
        self.act = self.addSubModel(Actuador(falla))
        self.alm = self.addSubModel(ModuloAlarmas())
        self.mon = self.addSubModel(Monitor())
        # acoplamientos internos (IC)
        self.connectPorts(self.gen.ordenMedica, self.ctrl.ordenMedica)
        self.connectPorts(self.genFB.finBolsa, self.ctrl.finBolsa)
        self.connectPorts(self.genCF.confirmacion, self.ctrl.confirmacion)
        self.connectPorts(self.genCF.confirmacion, self.alm.confirmacion)
        self.connectPorts(self.sen.sensorFlujo, self.ctrl.sensorFlujo)
        self.connectPorts(self.ctrl.ajustar, self.act.ajustar)
        self.connectPorts(self.ctrl.detener, self.act.detener)
        self.connectPorts(self.act.caudalActual, self.sen.caudalActual)
        self.connectPorts(self.ctrl.alarmaBaja, self.alm.alarmaBaja)
        self.connectPorts(self.ctrl.alarmaMedia, self.alm.alarmaMedia)
        self.connectPorts(self.ctrl.alarmaCritica, self.alm.alarmaCritica)
        # Monitor: solo observa, no participa de la logica de control.
        self.connectPorts(self.ctrl.ajustar, self.mon.ajustar)
        self.connectPorts(self.act.caudalActual, self.mon.caudalActual)
        self.connectPorts(self.ctrl.registro, self.mon.registro)
        self.connectPorts(self.alm.notificacion, self.mon.notificacion)
        self.connectPorts(self.sen.sensorFlujo, self.mon.sensorFlujo)
        # entradas externas (EIC): via alternativa para forzar un evento
        # puntual desde afuera, ademas de lo que generen genFB/genCF.
        self.connectPorts(self.finBolsa, self.ctrl.finBolsa)
        self.connectPorts(self.confirmacion, self.ctrl.confirmacion)
        self.connectPorts(self.confirmacion, self.alm.confirmacion)
        # salidas externas (EOC)
        self.connectPorts(self.ctrl.registro, self.registro)
        self.connectPorts(self.alm.notificacion, self.notificacion)
        self.connectPorts(self.sen.sensorFlujo, self.sensorFlujo)
        self.connectPorts(self.act.caudalActual, self.caudalActual)

    def select(self, imm):
        # Prioridad ante empates: Ctrl, Act, Sen, Alm, Mon, Gen, GenFB, GenCF.
        # Mon va antes de los generadores porque es un receptor reactivo
        # (como Ctrl/Act/Sen/Alm), no una fuente.
        for m in (self.ctrl, self.act, self.sen, self.alm, self.mon,
                  self.gen, self.genFB, self.genCF):
            if m in imm:
                return m
        return imm[0]
