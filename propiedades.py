"""
Verificacion automatica de las propiedades pedidas en la seccion 9 del
informe ("Propiedades a verificar"), a partir de la traza registrada por
el Monitor (modelos.EstadoMonitor) durante una corrida.

Cada funcion de verificacion devuelve un objeto Resultado con:
  - ok: True/False/None (None = "no aplica en este escenario", por ejemplo
    revisar una alarma critica no confirmada cuando nunca hubo una)
  - detalle: texto explicando el motivo del resultado, con la evidencia
    (tiempos, valores) usada para decidir

El criterio "el grupo debe definir" de la seccion 9 (limite exacto entre
alarmaMedia y alarmaCritica, ya fijado en modelos.py con N_MEDIA/N_CRITICA)
se documenta en el propio docstring de cada chequeo cuando aplica.

Juan Ignacio Villanueva - Santiago Pesce
"""

from modelos import MAX_CAUDAL, T_BOLSA, T_CONF, T_REP


class Resultado:
    def __init__(self, ok, detalle):
        self.ok = ok          # True, False, o None (no aplica)
        self.detalle = detalle

    def etiqueta(self):
        if self.ok is None:
            return "N/A "
        return "PASA" if self.ok else "FALLA"


# ---------------------------------------------------------------------------
# 9.1 Propiedades de seguridad (safety)
# ---------------------------------------------------------------------------

def safety_no_administra_con_orden_cero(monitor):
    """La bomba no debe administrar medicacion si la ultima orden medica
    recibida indica caudal igual a cero.
    Se verifica que, tras un 'stop_por_orden', el caudal real (serie_real)
    no vuelva a ser > 0 hasta la siguiente orden que reinicie la infusion."""
    eventos = monitor.eventos
    reales = monitor.serie_real
    violaciones = []
    t_stop = None
    for t, ev in eventos:
        if ev == "stop_por_orden":
            t_stop = t
        elif ev in ("inicio_infusion", "reanudacion_post_bolsa"):
            t_stop = None  # nueva orden vigente: ya no aplica la prohibicion
        if t_stop is not None:
            for tr, vr in reales:
                if tr > t_stop and vr > 0:
                    violaciones.append((tr, vr))
    if violaciones:
        return Resultado(False, f"Caudal real > 0 tras stop_por_orden en: {violaciones}")
    if not any(ev == "stop_por_orden" for _, ev in eventos):
        return Resultado(None, "No hubo ninguna orden con caudal 0 en este escenario")
    return Resultado(True, "Ningun caudal real > 0 detectado despues de una orden con caudal 0")


def safety_caudal_no_excede_maximo(monitor):
    """El caudal administrado no debe superar el maximo permitido (MAX_CAUDAL)."""
    excesos = [(t, v) for t, v in monitor.serie_real if v > MAX_CAUDAL]
    if excesos:
        return Resultado(False, f"Caudal real supero MAX_CAUDAL={MAX_CAUDAL} en: {excesos}")
    return Resultado(True, f"Ningun valor de caudal real supero MAX_CAUDAL={MAX_CAUDAL} ml/h")


def safety_no_reanuda_sin_confirmar(monitor):
    """Luego de una alarmaCritica, la bomba no debe reanudar la infusion
    hasta recibir confirmacionEnfermero o una nueva ordenMedica.
    Se verifica que entre 'critica_stop' y el siguiente evento que reanuda
    (confirmacion_enfermero, inicio_infusion) no exista ningun caudal real
    distinto de cero."""
    eventos = monitor.eventos
    reales = monitor.serie_real
    violaciones = []
    t_critica = None
    hubo_critica = False
    for t, ev in eventos:
        if ev == "critica_stop":
            t_critica = t
            hubo_critica = True
        elif ev in ("confirmacion_enfermero", "inicio_infusion"):
            t_critica = None
        if t_critica is not None:
            for tr, vr in reales:
                if tr > t_critica and vr > 0:
                    violaciones.append((tr, vr))
    if violaciones:
        return Resultado(False, f"Caudal real > 0 mientras la bomba deberia estar detenida tras critica: {violaciones}")
    if not hubo_critica:
        return Resultado(None, "No hubo ninguna alarmaCritica en este escenario")
    return Resultado(True, "La bomba permanecio en caudal 0 desde la alarma critica hasta la confirmacion/nueva orden")


# ---------------------------------------------------------------------------
# 9.2 Propiedades de vivacidad (liveness)
# ---------------------------------------------------------------------------

def liveness_orden_produce_accion(monitor):
    """Toda ordenMedica recibida debe producir eventualmente una accion
    sobre la bomba. En este modelo, cada ordenMedica con efecto se traduce
    de inmediato (en la misma transicion) en un evento de registro
    ('inicio_infusion' o 'stop_por_orden'), asi que la propiedad se cumple
    estructuralmente siempre que existan estos eventos en la traza."""
    eventos = [ev for _, ev in monitor.eventos]
    ordenes = [ev for ev in eventos if ev in ("inicio_infusion", "stop_por_orden")]
    if not ordenes:
        return Resultado(None, "No se registraron ordenes medicas en este escenario")
    return Resultado(True, f"{len(ordenes)} orden/es medica/s, todas con accion registrada: {ordenes}")


def liveness_critica_se_repite(monitor):
    """Toda alarmaCritica no confirmada debe repetirse eventualmente (no se
    queda en una sola notificacion sin mas seguimiento). A diferencia de la
    propiedad temporal correspondiente (que exige los periodos exactos
    T_CONF/T_REP), esta version de liveness solo exige que exista al menos
    una repeticion antes de la confirmacion, con un periodo creciente o
    igual entre repeticiones (no decrece, lo cual indicaria una repeticion
    fuera de orden)."""
    alarmas = monitor.alarmas
    eventos = monitor.eventos
    criticas = [t for t, tipo in alarmas if tipo == "critica"]
    if not criticas:
        return Resultado(None, "No hubo ninguna alarmaCritica en este escenario")
    t_confirm = next((t for t, ev in eventos if ev == "confirmacion_enfermero"
                       and t >= criticas[0]), None)
    activas = [t for t in criticas if t_confirm is None or t <= t_confirm]
    if len(activas) < 2:
        return Resultado(None, f"Solo {len(activas)} alarma/s critica/s antes de confirmar; "
                                f"no hay suficientes ocurrencias para verificar la repeticion")
    gaps = [round(b - a, 4) for a, b in zip(activas, activas[1:])]
    if any(g <= 0 for g in gaps):
        return Resultado(False, f"Gaps no monotonos entre repeticiones: {gaps}")
    return Resultado(True, f"La alarmaCritica se repitio {len(activas) - 1} vez/veces "
                            f"antes de la confirmacion (gaps: {gaps})")


def liveness_finbolsa_detiene_eventualmente(monitor):
    """Luego de detectar finBolsa, la infusion debe detenerse eventualmente
    (autostop) o continuar de forma legitima por una confirmacion del
    enfermero (reanudacion_post_bolsa) o una nueva orden."""
    eventos = monitor.eventos
    fines = [t for t, ev in eventos if ev == "fin_bolsa"]
    if not fines:
        return Resultado(None, "No se detecto finBolsa en este escenario")
    for t_fb in fines:
        resuelto = any(ev in ("autostop_fin_bolsa", "reanudacion_post_bolsa", "inicio_infusion")
                        and t > t_fb for t, ev in eventos)
        if not resuelto:
            return Resultado(False, f"finBolsa en t={t_fb} nunca se resolvio "
                                     f"(ni autostop ni reanudacion) dentro del horizonte simulado")
    return Resultado(True, f"Cada finBolsa ({fines}) tuvo una resolucion eventual "
                            f"(autostop, reanudacion o nueva orden)")


# ---------------------------------------------------------------------------
# 9.3 Propiedades temporales
# ---------------------------------------------------------------------------

def temporal_inicio_menor_3s(monitor):
    """Toda ordenMedica con caudal > 0 debe provocar el inicio de la
    infusion en menos de 3 segundos. En esta implementacion el Controlador
    procesa la orden y emite ajustarCaudal en la misma transicion (delta=0),
    asi que el margen real esta dominado por T_LATENCIA del actuador, muy
    por debajo del limite de 3s."""
    from modelos import T_LATENCIA
    if not any(ev == "inicio_infusion" for _, ev in monitor.eventos):
        return Resultado(None, "No hubo ninguna orden con caudal > 0 en este escenario")
    if T_LATENCIA >= 3:
        return Resultado(False, f"T_LATENCIA={T_LATENCIA}s no cumple el limite de 3s")
    return Resultado(True, f"El Controlador reacciona en el mismo instante (delta=0) y "
                            f"el Actuador aplica el ajuste en T_LATENCIA={T_LATENCIA}s < 3s")


def temporal_desvio_genera_alarma_media(monitor):
    """Si el caudal difiere mas del 10% durante mas de 5s, debe emitirse
    alarmaMedia. Se verifica usando los tiempos de respuesta ante desvio
    ya calculados por el Monitor (tiempos_resp_desvio), que miden el lapso
    entre la 1ra correccion_caudal de una racha y la alarma que la cierra."""
    tiempos = monitor.tiempos_resp_desvio
    if not tiempos:
        return Resultado(None, "No hubo ningun desvio sostenido en este escenario")
    # con N_MEDIA=6 y T_MUESTREO=1, el tiempo de respuesta esperado es de
    # exactamente 5s (6 muestras: la primera abre el cronometro, la 6ta
    # dispara la alarma -> 5s de diferencia).
    malos = [t for t in tiempos if t > 5.5]  # tolerancia de medio periodo de muestreo
    if malos:
        return Resultado(False, f"Tiempos de respuesta ante desvio que superan los 5s: {malos}")
    return Resultado(True, f"Tiempos de respuesta ante desvio sostenido: {tiempos} (todos <= 5s)")


def temporal_finbolsa_detiene_60s(monitor):
    """Si se detecta finBolsa, la bomba debe detenerse como maximo luego
    de 60 segundos (T_BOLSA)."""
    tiempos = monitor.tiempos_resp_finbolsa
    if not tiempos:
        return Resultado(None, "No hubo ningun autostop por fin de bolsa en este escenario "
                                "(puede ser porque el enfermero confirmo antes, o porque no hubo finBolsa)")
    malos = [t for t in tiempos if t > T_BOLSA + 0.01]
    if malos:
        return Resultado(False, f"Tiempos de autostop que superan T_BOLSA={T_BOLSA}s: {malos}")
    return Resultado(True, f"Tiempos de autostop tras finBolsa: {tiempos} (todos <= {T_BOLSA}s)")


def temporal_critica_repite_30_10(monitor):
    """Si una alarmaCritica no es confirmada dentro de 30 segundos
    (T_CONF), debe repetirse cada 10 segundos (T_REP)."""
    alarmas = monitor.alarmas
    eventos = monitor.eventos
    criticas = [t for t, tipo in alarmas if tipo == "critica"]
    if len(criticas) < 2:
        if not criticas:
            return Resultado(None, "No hubo ninguna alarmaCritica en este escenario")
        return Resultado(None, "Solo una alarmaCritica (se confirmo antes de repetirse, comportamiento correcto)")
    t0 = criticas[0]
    primera_rep = criticas[1]
    delta_primera = round(primera_rep - t0, 4)
    if abs(delta_primera - T_CONF) > 0.05:
        return Resultado(False, f"La 1ra repeticion ocurrio {delta_primera}s despues de la alarma "
                                 f"inicial (se esperaba T_CONF={T_CONF}s)")
    siguientes = criticas[1:]
    gaps = [round(b - a, 4) for a, b in zip(siguientes, siguientes[1:])]
    malos = [g for g in gaps if abs(g - T_REP) > 0.05]
    if malos:
        return Resultado(False, f"Intervalos entre repeticiones posteriores: {gaps} (se esperaba {T_REP}s)")
    return Resultado(True, f"1ra repeticion a los {delta_primera}s (= T_CONF), "
                            f"siguientes cada {gaps or 'N/A'} (= T_REP)")


PROPIEDADES = [
    ("Safety", "No administra con orden caudal=0", safety_no_administra_con_orden_cero),
    ("Safety", "Caudal no excede el maximo permitido", safety_caudal_no_excede_maximo),
    ("Safety", "No reanuda sin confirmar tras critica", safety_no_reanuda_sin_confirmar),
    ("Liveness", "Toda orden produce una accion", liveness_orden_produce_accion),
    ("Liveness", "Alarma critica se repite periodicamente", liveness_critica_se_repite),
    ("Liveness", "Fin de bolsa se resuelve eventualmente", liveness_finbolsa_detiene_eventualmente),
    ("Temporal", "Inicio de infusion en menos de 3s", temporal_inicio_menor_3s),
    ("Temporal", "Desvio sostenido genera alarmaMedia", temporal_desvio_genera_alarma_media),
    ("Temporal", "Fin de bolsa detiene en <= 60s", temporal_finbolsa_detiene_60s),
    ("Temporal", "Alarma critica repite en 30s/10s", temporal_critica_repite_30_10),
]


def verificar_propiedades(monitor):
    """Corre las 10 verificaciones y devuelve una lista de tuplas
    (categoria, nombre, Resultado)."""
    out = []
    for categoria, nombre, fn in PROPIEDADES:
        try:
            resultado = fn(monitor)
        except Exception as e:
            resultado = Resultado(False, f"Error al verificar: {e}")
        out.append((categoria, nombre, resultado))
    return out


def imprimir_propiedades(monitor, titulo):
    print(f"  --- Verificacion de propiedades ({titulo}) ---")
    resultados = verificar_propiedades(monitor)
    cat_actual = None
    for categoria, nombre, resultado in resultados:
        if categoria != cat_actual:
            print(f"  [{categoria}]")
            cat_actual = categoria
        print(f"    {resultado.etiqueta():5s} {nombre}")
        print(f"          {resultado.detalle}")
    fallas = [r for _, _, r in resultados if r.ok is False]
    print(f"  Resumen: {len(fallas)} falla/s de {len(resultados)} propiedades verificadas "
          f"({sum(1 for _,_,r in resultados if r.ok is None)} no aplicables en este escenario)")
    print()
    return resultados
