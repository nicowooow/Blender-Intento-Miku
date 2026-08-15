# -*- coding: utf-8 -*-
"""
04_corrective_drivers.py  --  Shape keys correctivos + drivers automaticos.

Crea, para cada articulacion, un shape key vacio (identico a la base, o sea
sin efecto hasta que lo esculpas) y le conecta un driver que lo activa segun
cuanto se doble el hueso.

Como se mide el angulo
----------------------
Se probo empiricamente el rig y el eje de flexion NO cae sobre un eje local
limpio (en el codo, ROT_X y ROT_Z dan exactamente el mismo resultado: la
bisagra esta a ~45 grados de los ejes del hueso). Por eso el driver no usa un
solo eje sino la MAGNITUD DEL SWING:

    sqrt(sx*sx + sz*sz)      con sx, sz = ROT_X / ROT_Z en LOCAL_SPACE
                             y rotation_mode = SWING_TWIST_Y

En espacio local el reposo es exactamente 0 sea cual sea la orientacion del
padre, y la magnitud del swing mide el doblez real venga por donde venga.
Esto ademas evita ROTATION_DIFF, que en este rig es inservible para cadera y
tobillo (en reposo ya marcan 180 grados y se satura).

Los correctivos de TORSION usan ROT_Y (el twist puro a lo largo del hueso),
con signo, porque pronacion y supinacion deforman distinto.

La curva es un F-curve con dos keyframes -- (0 grados, 0.0) y (angulo, 1.0) --
y extrapolacion constante, asi que no hace falta habilitar auto-run de scripts
y puedes reajustar la respuesta a mano en el Graph Editor.

Uso:
  blender -b modelo-base-fix.blend --python scripts/04_corrective_drivers.py -- \
      --out modelo-base-fix.blend [--object Body] [--no-twist] [--clear]
"""

import bpy
import os
import sys
import json
from math import radians

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _common import arg, flag, JOINTS, TWISTS, save

OUT = arg("--out")
OBJ = str(arg("--object", "Body"))
NO_TWIST = flag("--no-twist")
CLEAR = flag("--clear")
PREFIX = "corr_"


def ensure_basis(ob):
    if not ob.data.shape_keys:
        ob.shape_key_add(name="Basis", from_mix=False)
        print("   creado shape key 'Basis'")


def drop_existing(ob):
    """Borra correctivos previos de este script (para poder re-ejecutarlo)."""
    if not ob.data.shape_keys:
        return 0
    sk = ob.data.shape_keys
    names = [k.name for k in sk.key_blocks if k.name.startswith(PREFIX)]
    for n in names:
        path = 'key_blocks["%s"].value' % n
        try:
            sk.driver_remove(path)
        except Exception:
            pass
        ob.shape_key_remove(sk.key_blocks[n])
    return len(names)


def make_driver(sk, kb_name, arm_ob, bone, expression, variables, points):
    """Driver scripted + F-curve con keyframes explicitos."""
    path = 'key_blocks["%s"].value' % kb_name
    try:
        sk.driver_remove(path)
    except Exception:
        pass
    fcu = sk.driver_add(path)
    drv = fcu.driver
    drv.type = 'SCRIPTED'

    for vname, ttype in variables:
        var = drv.variables.new()
        var.name = vname
        var.type = 'TRANSFORMS'
        tgt = var.targets[0]
        tgt.id = arm_ob
        tgt.bone_target = bone
        tgt.transform_type = ttype
        tgt.transform_space = 'LOCAL_SPACE'
        tgt.rotation_mode = 'SWING_TWIST_Y'
    drv.expression = expression

    # driver_add() mete un modificador GENERATOR (y = x) que ignora los
    # keyframes; hay que quitarlo para que mande la curva.
    for m in list(fcu.modifiers):
        fcu.modifiers.remove(m)
    for x, y in points:
        fcu.keyframe_points.insert(x, y)
    for kp in fcu.keyframe_points:
        kp.interpolation = 'LINEAR'
    fcu.extrapolation = 'CONSTANT'
    fcu.update()
    return fcu, drv


def main():
    ob = bpy.data.objects.get(OBJ)
    if not ob or ob.type != 'MESH':
        print("!! no existe la malla '%s'" % OBJ)
        return
    arm_ob = None
    arm_mod = None
    for m in ob.modifiers:
        if m.type == 'ARMATURE' and m.object:
            arm_ob, arm_mod = m.object, m
            break
    if not arm_ob:
        print("!! '%s' no tiene modificador Armature" % OBJ)
        return

    print("=" * 74)
    print("SHAPE KEYS CORRECTIVOS + DRIVERS  sobre '%s'" % OBJ)
    print("=" * 74)

    ensure_basis(ob)
    if CLEAR:
        n = drop_existing(ob)
        print("   borrados %d correctivos previos" % n)
        if OUT:
            save(OUT)
        return

    n = drop_existing(ob)
    if n:
        print("   reemplazando %d correctivos previos" % n)

    sk = ob.data.shape_keys
    created = []

    # ---- articulaciones (flexion) ----
    for key, bone, label, target_deg, kind in JOINTS:
        if bone not in arm_ob.data.bones:
            print("   !! hueso '%s' no existe, salto %s" % (bone, key))
            continue
        name = PREFIX + key
        kb = ob.shape_key_add(name=name, from_mix=False)
        kb.slider_min, kb.slider_max = 0.0, 1.0
        make_driver(sk, name, arm_ob, bone,
                    'sqrt(sx*sx + sz*sz)',
                    [('sx', 'ROT_X'), ('sz', 'ROT_Z')],
                    [(0.0, 0.0), (radians(target_deg), 1.0)])
        created.append({"shape_key": name, "bone": bone, "label": label,
                        "tipo": kind, "activa_a_grados": target_deg,
                        "driver": "sqrt(sx^2+sz^2) swing local"})
        print("   %-28s <- %-20s  0 -> %d deg  (%s)" % (name, bone, target_deg, kind))

    # ---- torsion de antebrazo (este rig no tiene huesos twist) ----
    if not NO_TWIST:
        for key, bone, label, target_deg in TWISTS:
            if bone not in arm_ob.data.bones:
                continue
            name = PREFIX + key
            kb = ob.shape_key_add(name=name, from_mix=False)
            kb.slider_min, kb.slider_max = 0.0, 1.0
            make_driver(sk, name, arm_ob, bone,
                        'ty',
                        [('ty', 'ROT_Y')],
                        [(0.0, 0.0), (radians(target_deg), 1.0)])
            created.append({"shape_key": name, "bone": bone, "label": label,
                            "tipo": "twist", "activa_a_grados": target_deg,
                            "driver": "ROT_Y twist local"})
            print("   %-28s <- %-20s  0 -> %+d deg  (twist)" % (name, bone, target_deg))

    # ---- dejar el archivo listo para esculpir en pose ----
    arm_mod.show_in_editmode = True
    arm_mod.show_on_cage = True
    ob.show_only_shape_key = False
    print("\n   Armature modifier: show_in_editmode=ON, show_on_cage=ON")
    print("   (permite esculpir el correctivo viendo la malla ya deformada)")

    # ---- validacion ----
    bad = []
    for d in sk.animation_data.drivers if sk.animation_data else []:
        if not d.driver.is_valid:
            bad.append(d.data_path)
    print("\n   drivers creados: %d   invalidos: %d" % (
        len(sk.animation_data.drivers) if sk.animation_data else 0, len(bad)))
    for b in bad:
        print("     !! %s" % b)

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "correctives_report.json"), "w", encoding="utf-8") as f:
        json.dump(created, f, indent=1, ensure_ascii=False)

    if OUT:
        save(OUT)


main()
