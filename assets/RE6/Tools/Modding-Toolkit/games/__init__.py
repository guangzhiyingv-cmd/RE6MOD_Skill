from . import mhwi, mhws, mhrs, re4, re9, owots

modules = [
    mhwi,
    mhws,
    mhrs,
    re4,
    re9,
    owots,
]

def register():
    for mod in modules:
        mod.register()

def unregister():
    for mod in reversed(modules):
        mod.unregister()
