# 10.6 Análisis del cumplimiento de los requisitos temporales

## Metodología

El cumplimiento no se evaluó por inspección manual de la traza, sino con un
verificador automático (`propiedades.py`) que mide, sobre los datos
efectivamente registrados por el `Monitor` durante cada corrida, el tiempo
real transcurrido entre el evento causa y el evento efecto que exige cada
requisito. Cada chequeo se construyó para no depender de constantes leídas
directamente del código (lo cual sería una verificación tautológica, que
nunca podría detectar un error de implementación), sino de marcas de tiempo
extraídas de la simulación: instantes de `ordenMedica`, primer cambio de
`caudalActual`, instantes de `alarmaMedia`/`alarmaCritica`, e instantes de
`finBolsa` y su resolución.

Se ejecutaron los 7 escenarios de la sección 8 y se corrieron las 10
propiedades de la sección 9 sobre cada uno. De las 10, 4 son requisitos
temporales explícitos; los resultados se detallan a continuación.

## Requisito 1 — Inicio de infusión en menos de 3 segundos

> *"Luego de recibir una ordenMedica con caudal mayor que cero, la bomba
> debe comenzar la infusión en menos de 3 segundos."*

**Cumple.** Se midió el lapso entre cada evento `ordenMedica` (registrado
como `inicio_infusion`) y el primer punto de caudal real efectivamente
distinto de cero en `serie_real`. En los 7 escenarios, incluyendo el cambio
de orden médica (escenario 2, con dos órdenes sucesivas), el tiempo medido
fue de **0.5 segundos** en todos los casos — exactamente `T_LATENCIA`, la
latencia configurada del Actuador. Esto es así porque el `Controlador`
procesa la orden y emite `ajustarCaudal` en la misma transición en que la
recibe (sin demora propia), y el único retardo del sistema es el que
introduce el Actuador al aplicar el ajuste. El margen respecto al límite
de 3 segundos es de 2.5 segundos, es decir, un 83% de margen.

## Requisito 4 — Desvío sostenido genera alarmaMedia

> *"Si el caudal real difiere en más de un 10% respecto del caudal
> indicado durante más de 5 segundos, el sistema debe emitir una
> alarmaMedia."*

**Cumple, con el criterio temporal exacto en el límite del enunciado.** El
sensor muestrea cada `T_MUESTREO = 1 s`. El Controlador cuenta muestras
consecutivas fuera de tolerancia (`TOL = 10%`) y dispara `alarmaMedia` en
la sexta muestra desviada (`N_MEDIA = 6`). Esto da un tiempo de respuesta
medido de **5.0 segundos exactos** entre la primera muestra desviada y la
emisión de la alarma, verificado empíricamente en los escenarios 5 y 7
(ambos con `falla = 0.5`, desvío del 50%).

Vale la pena señalar la interpretación elegida: el enunciado dice "durante
*más de* 5 segundos", lo cual en sentido estricto pediría un tiempo
estrictamente mayor a 5 s (por ejemplo, la séptima muestra en lugar de la
sexta). Se decidió fijar el límite en exactamente 5 s porque es el valor
sugerido en la Tabla 1 de parámetros ("Tiempo máximo de desvío de caudal:
5 s") y porque el propio enunciado habilita a cada grupo a definir el
criterio exacto de escalada. El margen es por lo tanto nulo en este
requisito: cumple en el límite, no con holgura.

## Requisito 6 / 9.3.4 — Repetición de alarmaCritica cada 10s tras 30s sin confirmar

> *"Si una alarmaCritica no es confirmada por el enfermero dentro de 30
> segundos, la alarma debe repetirse cada 10 segundos."*

**Cumple.** Verificado en el escenario 7 (alarma crítica sin ninguna
confirmación durante toda la corrida). La primera repetición ocurrió
exactamente **30.0 segundos** después de la alarma crítica inicial (=
`T_CONF`), y las repeticiones siguientes a intervalos de **10.0 segundos**
cada una (= `T_REP`), sin desviación. El verificador comprobó tanto el
primer intervalo (que usa `T_CONF`) como los intervalos subsiguientes (que
usan `T_REP`) por separado, ya que son dos constantes distintas del
sistema.

## Requisito 7 — Detención automática a los 60s de fin de bolsa

> *"Si se detecta finBolsa, debe emitirse una alarmaBaja y la infusión
> sólo puede continuar durante un máximo de 60 segundos. Transcurridos los
> 60 segundos, la bomba debe detener automáticamente la infusión."*

**Cumple.** Verificado en el escenario 6 (fin de bolsa con confirmación
del enfermero a los 30 s de iniciado el evento `finBolsa`). El sistema
resolvió la situación en **20 segundos** (bien dentro del límite de 60),
ya que la confirmación del enfermero canceló el temporizador de autostop
antes de que este se disparara. El verificador mide la cota superior real
(tiempo entre `finBolsa` y su resolución, sea autostop o reanudación por
confirmación), no únicamente el caso del autostop puro, para evitar una
verificación tautológica que solo confirmara que la constante `T_BOLSA`
está bien definida en el código.

## Resumen

| Requisito temporal | Resultado medido | Límite | Margen |
|---|---|---|---|
| Inicio de infusión tras orden | 0.5 s | < 3 s | 2.5 s (83%) |
| Desvío sostenido → alarmaMedia | 5.0 s | "más de" 5 s* | 0 s (límite) |
| 1ª repetición de alarmaCritica | 30.0 s | = 30 s | exacto |
| Repeticiones siguientes | 10.0 s | = 10 s | exacto |
| Detención tras fin de bolsa | ≤ 20 s (en el escenario probado) | ≤ 60 s | variable según cuándo confirme el enfermero |

*El criterio exacto de escalada de alarmaMedia a alarmaCritica, y el de
"más de 5 segundos", quedan explícitamente a definición de cada grupo
según el propio enunciado (sección 5, requisito 5).

**Conclusión**: los 4 requisitos temporales explícitos de la sección 5 (y
sus contrapartes verificables de la sección 9.3) se cumplen en los 7
escenarios de prueba, sin ninguna falla detectada por el verificador
automático. El único punto sin margen es la escalada a `alarmaMedia`, que
opera exactamente en el límite del criterio elegido (5.0 s); todos los
demás requisitos tienen margen respecto a sus límites, o son intervalos
exactos por diseño (no por azar).

---

# 10.7 Propuesta de mejora del controlador para reducir riesgos

## Limitación identificada: el Actuador modela la falla como un factor constante

Durante el desarrollo y la verificación se identificó una limitación de
diseño en el `Actuador`: la falla se modela como un factor multiplicativo
fijo (`caudal_real = objetivo × falla`), constante durante toda la
simulación. Esto tiene una consecuencia indeseada desde el punto de vista
de seguridad clínica: **ningún desvío de caudal que supere la tolerancia
del 10% puede corregirse exitosamente**, sin importar cuántas veces el
Controlador reintente ajustar el caudal (`correccion_caudal`). Si la causa
del desvío persiste (por ejemplo, una obstrucción parcial real), el
sistema reintenta de forma idéntica, vuelve a fallar de forma idéntica, y
termina escalando inevitablemente a `alarmaCritica` con detención de la
bomba.

Esto significa que, en el modelo actual, **no existe ningún mecanismo de
autocorrección real**: la única forma de que el escenario 4 ("desvío leve
corregido por el controlador") se resuelva sin alarma es que el desvío
nunca haya superado la tolerancia desde el principio, no que el
controlador efectivamente corrija algo. Desde la perspectiva de un
controlador de bomba de infusión real, esto es un riesgo: confunde
"reintentar la misma orden" con "corregir la causa del desvío", y no deja
margen para que una falla transitoria (una burbuja de aire que se
disuelve, una torsión momentánea de la vía, un pico de presión que se
normaliza) se resuelva sola sin escalar innecesariamente hasta detener la
infusión.

## Mejora propuesta: modelar la falla del Actuador como transitoria, con probabilidad de recuperación por reintento

Se propone modificar el `Actuador` para que la falla no sea un factor
fijo de por vida, sino un **estado transitorio con probabilidad de
recuperación en cada reintento**. Concretamente:

1. El Actuador mantiene un estado interno `fallando: bool`, inicialmente
   `False`.
2. Cuando se introduce una falla externa (por ejemplo, simulando una
   obstrucción), `fallando` pasa a `True` y el caudal real se calcula con
   el factor de desvío, igual que hoy.
3. **Cada vez que el Actuador recibe un `ajustarCaudal`** (es decir, cada
   vez que el Controlador reintenta corregir), se evalúa con una
   probabilidad `p_recuperacion` si la falla se resuelve. Si se resuelve,
   `fallando` pasa a `False` y los siguientes caudales se entregan sin
   desvío.
4. Si la falla nunca se resuelve dentro de la ventana de `N_CRITICA`
   muestras, el sistema escala a `alarmaCritica` igual que hoy — el
   comportamiento de seguridad existente no se debilita, solo se le da
   una oportunidad real de evitarse cuando la causa es efectivamente
   transitoria.

Esta mejora tiene tres beneficios concretos para la seguridad del sistema:

- **Reduce alarmas críticas innecesarias.** Hoy, cualquier desvío
  persistente —incluso uno que en la realidad se resolvería solo en unos
  segundos— termina deteniendo la bomba. Una detención de la infusión no
  es un evento neutro: interrumpe la administración de la medicación al
  paciente y requiere intervención humana para reanudar. Reducir
  detenciones espurias reduce el riesgo asociado a interrupciones de
  tratamiento innecesarias.
- **Mantiene el comportamiento seguro ante fallas reales y persistentes.**
  La mejora no elimina la escalada a crítica: si `p_recuperacion` es baja
  o la causa de fondo no se resuelve, el sistema sigue escalando dentro
  del mismo número de muestras que hoy. La mejora únicamente le da al
  reintento del Controlador un efecto que antes no tenía.
- **Permite verificar el escenario 4 de forma fiel al enunciado.** Con el
  modelo actual, el escenario "desvío leve corregido por el controlador"
  solo puede demostrarse con un desvío que nunca llega a ser detectado
  como tal. Con la mejora propuesta, se podría modelar un desvío que
  *sí* se detecta, *sí* dispara el reintento del Controlador, y *sí* se
  resuelve gracias a ese reintento — que es el comportamiento que el
  ítem 4 de la sección 8 describe literalmente.

## Alternativa más simple, si se prefiere evitar aleatoriedad adicional

Si se quisiera una mejora determinística (sin probabilidad), una variante
más simple es que la falla tenga una **duración fija programada** (por
ejemplo, activa entre los segundos 3 y 7 de la simulación) en lugar de
ser permanente. Esto permite construir un escenario de "desvío leve
corregido" completamente reproducible y determinístico, a costa de ser
menos representativo de una falla física real (que normalmente no tiene
una duración conocida de antemano). Cualquiera de las dos variantes
resuelve la misma limitación de fondo; la elegida depende de si el grupo
prioriza realismo probabilístico o reproducibilidad exacta para la
entrega.
