# Bomba de infusión — modelo DEVS

Implementación en PythonPDEVS del controlador de una bomba de infusión
(versión reducida), para la materia Simulación de Sistemas (UNRC).

Autores: Juan Ignacio Villanueva y Santiago Pesce.

## Archivos

- `modelos.py`: los 5 modelos atómicos (generador de órdenes, sensor de flujo,
  actuador, controlador y módulo de alarmas) y el modelo acoplado `BombaInfusion`.
- `escenarios.py`: los escenarios de simulación y la corrida que imprime las trazas.
- `compat.py`: parche para que pypdevs corra en Python 3.13 o superior.

## Cómo correrlo

Crear el entorno e instalar las dependencias:

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

Correr los escenarios:

    python escenarios.py

pypdevs se instala desde un fork porque el original quedó en Python 2. El
`compat.py` resuelve además un cambio de Python 3.13 (PEP 667) que rompía el
armado del scheduler del simulador.
