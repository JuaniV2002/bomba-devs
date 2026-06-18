"""
Generacion de los resultados pedidos en la seccion 11 del informe
("Resultados esperados") a partir del estado final del Monitor.

Se generan 7 graficos/reportes por escenario:
  1. Caudal indicado vs caudal real (linea de tiempo)
  2. Estado de la bomba a lo largo del tiempo (escalones)
  3. Registro de alarmas generadas (eventos puntuales por tipo)
  4. Tiempo de respuesta ante desvios de caudal (barras por ocurrencia)
  5. Tiempo de respuesta ante fin de bolsa (barras por ocurrencia)
  6. Cantidad de detenciones preventivas (resumen numerico)
  7. Porcentaje de tiempo con infusion correcta (resumen numerico)

Los graficos 1-5 se guardan como PNG; 6 y 7 son metricas escalares que se
imprimen junto con el resto y tambien se anotan en el grafico de estado.

Juan Ignacio Villanueva - Santiago Pesce
"""

import os
import matplotlib
matplotlib.use("Agg")  # sin display: solo generar archivos PNG
import matplotlib.pyplot as plt


MODOS_ORDEN = ["idle", "infund", "bolsa", "detenida"]
COLOR_ALARMA = {"baja": "#d4a017", "media": "#e8741c", "critica": "#c0392b"}


def _step_series(serie, t_final):
    """Convierte [(t, valor), ...] en listas (xs, ys) aptas para plt.step,
    repitiendo el ultimo valor hasta t_final."""
    if not serie:
        return [0, t_final], [None, None]
    xs, ys = [], []
    for (t, v) in serie:
        xs.append(t)
        ys.append(v)
    if xs[-1] < t_final:
        xs.append(t_final)
        ys.append(ys[-1])
    return xs, ys


def graficar_caudal(monitor, t_final, titulo, carpeta):
    fig, ax = plt.subplots(figsize=(9, 4))
    if monitor.serie_indicado:
        xs, ys = _step_series(monitor.serie_indicado, t_final)
        ax.step(xs, ys, where="post", label="Caudal indicado", color="#1f77b4", linewidth=2)
    if monitor.serie_real:
        xs_r = [t for t, _ in monitor.serie_real]
        ys_r = [v for _, v in monitor.serie_real]
        ax.plot(xs_r, ys_r, label="Caudal real", color="#d62728", linewidth=1.2,
                 marker=".", markersize=3, alpha=0.85)
    ax.set_xlabel("Tiempo (s)")
    ax.set_ylabel("Caudal (ml/h)")
    ax.set_title(f"{titulo}\nCaudal indicado vs caudal real")
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(carpeta, "01_caudal_indicado_vs_real.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def graficar_estado(monitor, t_final, titulo, carpeta):
    fig, ax = plt.subplots(figsize=(9, 3))
    xs, ys = _step_series(monitor.serie_modo, t_final)
    y_num = [MODOS_ORDEN.index(m) for m in ys]
    ax.step(xs, y_num, where="post", color="#2c3e50", linewidth=2)
    ax.set_yticks(range(len(MODOS_ORDEN)))
    ax.set_yticklabels(MODOS_ORDEN)
    ax.set_xlabel("Tiempo (s)")
    ax.set_ylabel("Estado de la bomba")
    ax.set_title(f"{titulo}\nEstado de la bomba a lo largo del tiempo")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(carpeta, "02_estado_bomba.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def graficar_alarmas(monitor, t_final, titulo, carpeta):
    fig, ax = plt.subplots(figsize=(9, 2.6))
    tipos = ["baja", "media", "critica"]
    for tipo in tipos:
        xs = [t for t, ti in monitor.alarmas if ti == tipo]
        ys = [tipos.index(tipo)] * len(xs)
        ax.scatter(xs, ys, color=COLOR_ALARMA[tipo], label=tipo, s=60, zorder=3)
    ax.set_yticks(range(len(tipos)))
    ax.set_yticklabels(tipos)
    ax.set_xlim(0, t_final)
    ax.set_xlabel("Tiempo (s)")
    ax.set_title(f"{titulo}\nRegistro de alarmas generadas")
    ax.legend(loc="upper right")
    ax.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    path = os.path.join(carpeta, "03_registro_alarmas.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _graficar_tiempos_respuesta(tiempos, titulo_metrica, archivo, titulo, carpeta):
    fig, ax = plt.subplots(figsize=(6, 3.5))
    if tiempos:
        idx = list(range(1, len(tiempos) + 1))
        ax.bar(idx, tiempos, color="#16a085")
        ax.set_xticks(idx)
        ax.set_xlabel("Ocurrencia")
        for i, v in zip(idx, tiempos):
            ax.text(i, v, f"{v:.2f}s", ha="center", va="bottom", fontsize=9)
    else:
        ax.text(0.5, 0.5, "Sin ocurrencias en este escenario", ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color="gray")
        ax.set_xticks([])
    ax.set_ylabel("Tiempo de respuesta (s)")
    ax.set_title(f"{titulo}\n{titulo_metrica}")
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    path = os.path.join(carpeta, archivo)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def graficar_resp_desvio(monitor, titulo, carpeta):
    return _graficar_tiempos_respuesta(
        monitor.tiempos_resp_desvio,
        "Tiempo de respuesta ante desvios de caudal",
        "04_tiempo_respuesta_desvio.png", titulo, carpeta)


def graficar_resp_finbolsa(monitor, titulo, carpeta):
    return _graficar_tiempos_respuesta(
        monitor.tiempos_resp_finbolsa,
        "Tiempo de respuesta ante fin de bolsa",
        "05_tiempo_respuesta_finbolsa.png", titulo, carpeta)


def generar_graficos(monitor, t_final, titulo, carpeta="graficos"):
    """Genera los 5 graficos (1-5) como PNG y devuelve un dict con las
    metricas escalares (6-7) ademas de las rutas de los archivos."""
    os.makedirs(carpeta, exist_ok=True)
    monitor.cerrar(t_final)  # cierra tramos/series abiertos al final

    rutas = {
        "caudal": graficar_caudal(monitor, t_final, titulo, carpeta),
        "estado": graficar_estado(monitor, t_final, titulo, carpeta),
        "alarmas": graficar_alarmas(monitor, t_final, titulo, carpeta),
        "resp_desvio": graficar_resp_desvio(monitor, titulo, carpeta),
        "resp_finbolsa": graficar_resp_finbolsa(monitor, titulo, carpeta),
    }
    metricas = {
        "detenciones_preventivas": monitor.detenciones_preventivas,
        "pct_infusion_correcta": monitor.tiempo_infusion_correcta_pct(t_final),
        "tiempos_resp_desvio": list(monitor.tiempos_resp_desvio),
        "tiempos_resp_finbolsa": list(monitor.tiempos_resp_finbolsa),
    }
    return rutas, metricas


def imprimir_metricas(metricas, titulo):
    print(f"  --- Metricas ({titulo}) ---")
    print(f"  Detenciones preventivas: {metricas['detenciones_preventivas']}")
    print(f"  %% tiempo con infusion correcta: {metricas['pct_infusion_correcta']:.1f}%%".replace("%%", "%"))
    if metricas["tiempos_resp_desvio"]:
        prom = sum(metricas["tiempos_resp_desvio"]) / len(metricas["tiempos_resp_desvio"])
        print(f"  Tiempo de respuesta ante desvio (prom.): {prom:.2f} s "
              f"({len(metricas['tiempos_resp_desvio'])} ocurrencia/s)")
    else:
        print("  Tiempo de respuesta ante desvio: sin ocurrencias")
    if metricas["tiempos_resp_finbolsa"]:
        prom = sum(metricas["tiempos_resp_finbolsa"]) / len(metricas["tiempos_resp_finbolsa"])
        print(f"  Tiempo de respuesta ante fin de bolsa (prom.): {prom:.2f} s "
              f"({len(metricas['tiempos_resp_finbolsa'])} ocurrencia/s)")
    else:
        print("  Tiempo de respuesta ante fin de bolsa: sin ocurrencias")
    print()
