from importlib import import_module
import sys

sys.modules[__name__] = import_module('odessy.authority.command_bridge')

