# -*- coding: utf-8 -*-
"""
05_verify_deformation.py  --  Mide el pellizco real de la malla al doblar.

No mira los pesos: dobla de verdad cada articulacion, evalua la malla
deformada y compara el area de cada cara contra su area en reposo.
Una cara que colapsa (ratio -> 0) es literalmente un pellizco.

Se desactivan temporalmente los modificadores que no son el Armature
(Solidify / Mask) para comparar malla contra malla sin cambios de topologia.

Metricas por articulacion:
  ratio_min     area minima relativa de una cara (1.0 = sin distorsion)
  caras_<0.30   numero de caras que pierden mas del 70% de su area
  ratio_p05     percentil 5 de los ratios (peor cola, sin el ruido del minimo)

Uso:
  blender -b modelo-base.blend --python scripts/05_verify_deformation.py -- \
      [--object Body] [--angle 100] [--json out.json]
"""

import bpy
import os
import sys
import json
from math import radians
from mathutils import Vector, Euler

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _common import (arg, flag, joint_radius, joint_region, bone_space_coords,
                     weight_map, JOINTS, FINGER_JOINTS)

OBJ = str(arg("--object", "Body"))
ANGLE = float(arg("--angle", 100.0))
JSON_OUT = arg("--json")
TEST_JOINTS = JOINTS + (FINGER_JOINTS if flag("--fingers") else [])


def clear_pose(arm_ob):
    for pb in arm_ob.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.location = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()


def best_flex_axis(arm_ob, bone, deg=40.0):
    """
    Eje+signo local que mas ACERCA la punta del hueso a la cabeza del padre:
    esa es, por definicion, la flexion de la articulacion.
    """
    pb = arm_ob.pose.bones[bone]
    ph = arm_ob.matrix_world @ pb.parent.head
    best, rows = None, []
    for ai in (0, 1, 2):
        for sgn in (1, -1):
            pb.rotation_mode = 'XYZ'
            e = [0.0, 0.0, 0.0]
            e[ai] = radians(deg) * sgn
            pb.rotation_euler = Euler(e, 'XYZ')
            bpy.context.view_layer.update()
            rows.append((((arm_ob.matrix_world @ pb.tail) - ph).length, ai, sgn))
            pb.rotation_euler = Euler((0.0, 0.0, 0.0), 'XYZ')
    bpy.context.view_layer.update()
    pb.rotation_mode = 'QUATERNION'
    pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    rows.sort()
    return rows[0][1], rows[0][2]


def face_areas(ob, dg):
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    areas = [p.area for p in me.polygons]
    ev.to_mesh_clear()
    return areas


def main():
    ob = bpy.data.objects.get(OBJ)
    if not ob or ob.type != 'MESH':
        print("!! no existe la malla '%s'" % OBJ)
        return
    arm_ob = None
    for m in ob.modifiers:
        if m.type == 'ARMATURE' and m.object:
            arm_ob = m.object
            break
    if not arm_ob:
        print("!! '%s' no tiene modificador Armature" % OBJ)
        return

    # aislar el Armature: sin Solidify/Mask la topologia no cambia
    restore = []
    for m in ob.modifiers:
        if m.type != 'ARMATURE':
            restore.append((m, m.show_viewport))
            m.show_viewport = False

    prev_pos = arm_ob.data.pose_position
    arm_ob.data.pose_position = 'POSE'
    clear_pose(arm_ob)
    dg = bpy.context.evaluated_depsgraph_get()
    rest_areas = face_areas(ob, dg)

    coords = bone_space_coords(ob, arm_ob)
    wmap = weight_map(ob, 0.01)

    print("=" * 74)
    print("VERIFICACION DE DEFORMACION  '%s'  flexion=%.0f grados" % (OBJ, ANGLE))
    print("=" * 74)
    print("%-12s %-20s %8s %10s %10s %8s" % (
        "junta", "hueso", "caras", "ratio_min", "ratio_p05", "<0.30"))

    results = []
    for key, bone, label, target, kind in TEST_JOINTS:
        if bone not in arm_ob.data.bones:
            continue
        r = joint_radius(ob, arm_ob, bone, coords, wmap)
        if r <= 0.0:
            continue
        region = set(joint_region(ob, arm_ob, bone, r * 1.5, coords, wmap).keys())
        if not region:
            continue
        faces = [i for i, p in enumerate(ob.data.polygons)
                 if any(v in region for v in p.vertices)]
        if not faces:
            continue

        ai, sgn = best_flex_axis(arm_ob, bone)
        pb = arm_ob.pose.bones[bone]
        pb.rotation_mode = 'XYZ'
        e = [0.0, 0.0, 0.0]
        e[ai] = radians(ANGLE) * sgn
        pb.rotation_euler = Euler(e, 'XYZ')
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        posed = face_areas(ob, dg)

        ratios = []
        for i in faces:
            a0 = rest_areas[i]
            if a0 > 1e-9:
                ratios.append(posed[i] / a0)
        ratios.sort()
        if not ratios:
            continue
        p05 = ratios[max(0, int(0.05 * len(ratios)) - 1)]
        bad = sum(1 for x in ratios if x < 0.30)
        print("%-12s %-20s %8d %10.3f %10.3f %8d" % (
            key, bone, len(ratios), ratios[0], p05, bad))
        results.append({"joint": key, "bone": bone, "label": label,
                        "faces": len(ratios), "ratio_min": round(ratios[0], 4),
                        "ratio_p05": round(p05, 4), "faces_below_030": bad})

        pb.rotation_euler = Euler((0.0, 0.0, 0.0), 'XYZ')
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bpy.context.view_layer.update()

    tot_bad = sum(r["faces_below_030"] for r in results)
    print("-" * 74)
    print("caras severamente colapsadas (total): %d" % tot_bad)

    # restaurar
    for m, vis in restore:
        m.show_viewport = vis
    arm_ob.data.pose_position = prev_pos

    if JSON_OUT:
        with open(JSON_OUT, "w", encoding="utf-8") as f:
            json.dump({"file": bpy.data.filepath, "angle": ANGLE,
                       "results": results, "total_bad": tot_bad}, f, indent=1)


main()
