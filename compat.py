"""
Parche de compatibilidad para PythonPDEVS en Python 3.13 o superior.

El fork instancia el scheduler con un exec() seguido de un eval(), confiando
en que el nombre importado por el exec quede visible en el alcance local de la
funcion. Python 3.13 cambio esa semantica (PEP 667: locals() y exec dentro de
funciones), asi que en 3.13/3.14 la simulacion falla con
'NameError: name SchedulerAH is not defined'.

Aca reescribimos setScheduler para importar e instanciar el scheduler de forma
directa con importlib, sin depender de ese comportamiento. En Python 3.12 o
anterior el resultado es identico.

Se aplica con solo importar este modulo, antes de simular.
"""

import importlib
from pypdevs.DEVS import RootDEVS
from pypdevs.util import EPSILON


def _set_scheduler(self, scheduler_type):
    modulo, clase = scheduler_type
    try:
        mod = importlib.import_module("pypdevs.schedulers." + modulo)
    except ImportError:
        mod = importlib.import_module(modulo)
    Scheduler = getattr(mod, clase)
    self.scheduler = Scheduler(self.component_set, EPSILON, len(self.models))


RootDEVS.setScheduler = _set_scheduler
