# -*- coding: utf-8 -*-
"""
03_rebuild_joint_weights.py  --  Bone Heat ACOTADO a las articulaciones.

Un Bone Heat global sobre este modelo destruiria los pesos originales de VRoid
(pecho J_Sec_*_Bust, cara, pelo) y perderia 'ocultar_ropa'.  En vez de eso:

  1. Duplica la malla, le quita modificadores/shape keys/vertex groups y le
     aplica Bone Heat limpio (parent_set ARMATURE_AUTO) -> pesos de referencia.
  2. Para cada articulacion, dentro de la banda de flexion (+-1 radio del
     miembro), REDISTRIBUYE entre el hueso padre y el hijo la masa de peso que
     el vertice ya tenia repartida entre esos dos huesos, usando la proporcion
     que dio Bone Heat.  La masa total que el vertice dedica a otros huesos
     (pecho, columna...) no se toca -> no se rompe nada fuera de la junta.
  3. Aplica un suavizado de pesos restringido a esa misma banda.
  4. Renormaliza y borra el duplicado.

El mezclado usa una caida suave (smoothstep): 100% Bone Heat en el centro de la
articulacion, 0% en el borde de la banda, para no crear un nuevo escalon.

Uso:
  blender -b modelo-base-fix.blend --python scripts/03_rebuild_joint_weights.py -- \
      --out modelo-base-fix.blend [--objects Body] [--strength 0.85] \
      [--smooth-factor 0.5] [--smooth-repeat 3] [--band-scale 1.0]
"""

import bpy
import os
import sys
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _common import (arg, flag, Activated, skinned_meshes, bone_space_coords,
                     weight_map, joint_frame, joint_radius, joint_region,
                     JOINTS, FINGER_JOINTS, save)

OUT = arg("--out")
OBJECTS = [s for s in str(arg("--objects", "Body")).split(",") if s]
STRENGTH = float(arg("--strength", 0.85))
SMOOTH_F = float(arg("--smooth-factor", 0.5))
SMOOTH_R = int(arg("--smooth-repeat", 3))
BAND_SCALE = float(arg("--band-scale", 1.0))
NO_SMOOTH = flag("--no-smooth")
NO_FINGERS = flag("--no-fingers")
ONLY = [s for s in str(arg("--joints", "")).split(",") if s]

# Articulaciones a recalcular: las principales y, salvo --no-fingers, las
# falanges (tienen recorrido amplio y son las que mas transiciones abruptas
# concentran en el export de VRoid).
#
# IMPORTANTE: Bone Heat NO gana siempre. Medido con 05_verify_deformation.py,
# mejora mucho codos, cuello y tobillos, pero EMPEORA axila, cadera y rodilla,
# donde los pesos originales de VRoid estan mejor afinados a mano. Por eso
# --joints permite aplicarlo solo donde demuestra que ayuda.
TARGET_JOINTS = JOINTS + ([] if NO_FINGERS else FINGER_JOINTS)
if ONLY:
    TARGET_JOINTS = [j for j in TARGET_JOINTS if j[0] in ONLY]


def smoothstep(x):
    """0..1 con derivada nula en los extremos."""
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def bone_heat_reference(ob, arm_ob):
    """Duplicado temporal con Bone Heat -> {vert: {bone_name: weight}}."""
    tmp = ob.copy()
    tmp.data = ob.data.copy()
    tmp.name = "__TMP_boneheat"
    bpy.context.scene.collection.objects.link(tmp)

    with Activated(tmp):
        if tmp.data.shape_keys:
            tmp.shape_key_clear()
        for m in list(tmp.modifiers):
            tmp.modifiers.remove(m)
        tmp.vertex_groups.clear()
        tmp.parent = None

    for o in list(bpy.context.selected_objects):
        o.select_set(False)
    tmp.select_set(True)
    arm_ob.select_set(True)
    bpy.context.view_layer.objects.active = arm_ob
    res = bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    print("   bone heat -> %s (%d grupos)" % (res, len(tmp.vertex_groups)))

    gname = {g.index: g.name for g in tmp.vertex_groups}
    heat = []
    zero = 0
    for v in tmp.data.vertices:
        w = {gname[g.group]: g.weight for g in v.groups
             if g.group in gname and g.weight > 0.0}
        if not w:
            zero += 1
        heat.append(w)
    print("   vertices sin peso en la referencia: %d" % zero)

    for o in list(bpy.context.selected_objects):
        o.select_set(False)
    data = tmp.data
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.meshes.remove(data)
    return heat


def redistribute(ob, arm_ob, heat, stats):
    """Mezcla la referencia Bone Heat dentro de la banda de cada articulacion."""
    coords = bone_space_coords(ob, arm_ob)
    wmap = weight_map(ob, 0.0)
    gname = {g.index: g.name for g in ob.vertex_groups}
    name2vg = {g.name: g for g in ob.vertex_groups}

    for key, bone, label, target, kind in TARGET_JOINTS:
        b = arm_ob.data.bones.get(bone)
        if not b or not b.parent:
            continue
        pair = [bone, b.parent.name]
        if any(n not in name2vg for n in pair):
            stats.append({"joint": key, "skipped": "falta vertex group"})
            continue

        radius = joint_radius(ob, arm_ob, bone, coords, weight_map(ob, 0.01))
        if radius <= 0.0:
            stats.append({"joint": key, "skipped": "radio 0"})
            continue
        band = radius * BAND_SCALE
        verts = joint_region(ob, arm_ob, bone, band, coords, wmap)

        touched = 0
        moved = 0.0
        for vi, t in verts.items():
            cur = wmap[vi]
            # masa que este vertice dedica hoy a la pareja padre/hijo
            mass = sum(cur.get(name2vg[n].index, 0.0) for n in pair)
            if mass <= 1e-6:
                continue
            h = heat[vi]
            hs = sum(h.get(n, 0.0) for n in pair)
            if hs <= 1e-6:
                continue

            # 1 en el centro de la articulacion, 0 en el borde de la banda
            falloff = smoothstep(1.0 - abs(t) / band) * STRENGTH
            if falloff <= 0.0:
                continue

            for n in pair:
                vg = name2vg[n]
                old = cur.get(vg.index, 0.0)
                new = (1.0 - falloff) * old + falloff * (mass * h.get(n, 0.0) / hs)
                if abs(new - old) > 1e-6:
                    vg.add([vi], new, 'REPLACE')
                    cur[vg.index] = new
                    moved += abs(new - old)
            touched += 1

        stats.append({"joint": key, "bone": bone, "label": label,
                      "radius": round(radius, 4), "band": round(band, 4),
                      "verts_in_band": len(verts), "verts_modified": touched,
                      "weight_moved": round(moved, 3)})
        print("   %-12s %-20s radio=%.4f banda=%.4f verts=%-4d modificados=%-4d "
              "peso_movido=%.2f" % (key, bone, radius, band, len(verts), touched, moved))


def smooth_bands(ob, arm_ob):
    """Suaviza los pesos solo dentro de las bandas de flexion."""
    coords = bone_space_coords(ob, arm_ob)
    wmap01 = weight_map(ob, 0.01)
    sel = set()
    for key, bone, label, target, kind in TARGET_JOINTS:
        b = arm_ob.data.bones.get(bone)
        if not b or not b.parent:
            continue
        r = joint_radius(ob, arm_ob, bone, coords, wmap01)
        if r <= 0.0:
            continue
        sel.update(joint_region(ob, arm_ob, bone, r * BAND_SCALE,
                                coords, wmap01).keys())

    if not sel:
        return 0
    with Activated(ob):
        bpy.ops.object.mode_set(mode='OBJECT')
        for v in ob.data.vertices:
            v.select = (v.index in sel)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.vertex_group_smooth(group_select_mode='BONE_DEFORM',
                                           factor=SMOOTH_F, repeat=SMOOTH_R,
                                           expand=0.0)
        bpy.ops.object.mode_set(mode='OBJECT')
        for v in ob.data.vertices:
            v.select = False
    return len(sel)


def abrupt_count(ob, arm_ob):
    """Aristas con transicion abrupta entre huesos (metrica de control)."""
    gname = {g.index: g.name for g in ob.vertex_groups}
    deform = {b.name for b in arm_ob.data.bones if b.use_deform}
    W = [{g.group: g.weight for g in v.groups
          if gname.get(g.group) in deform and g.weight > 0.01}
         for v in ob.data.vertices]
    n = 0
    for e in ob.data.edges:
        a, c = e.vertices
        wa, wb = W[a], W[c]
        if not wa or not wb:
            continue
        keys = set(wa) | set(wb)
        if sum(abs(wa.get(k, 0.0) - wb.get(k, 0.0)) for k in keys) >= 0.75:
            n += 1
    return n


def main():
    print("=" * 74)
    print("BONE HEAT ACOTADO A ARTICULACIONES")
    print("strength=%.2f  band_scale=%.2f  smooth=%.2fx%d" % (
        STRENGTH, BAND_SCALE, SMOOTH_F, SMOOTH_R))
    print("=" * 74)

    targets = {ob.name: (ob, arm) for ob, arm in skinned_meshes()}
    report = []
    for name in OBJECTS:
        if name not in targets:
            print("!! malla '%s' no encontrada o sin modificador Armature" % name)
            continue
        ob, arm_ob = targets[name]
        print("\n[%s]" % name)
        before = abrupt_count(ob, arm_ob)
        print("   aristas abruptas ANTES: %d" % before)

        heat = bone_heat_reference(ob, arm_ob)
        stats = []
        redistribute(ob, arm_ob, heat, stats)

        nsel = 0 if NO_SMOOTH else smooth_bands(ob, arm_ob)
        if nsel:
            print("   suavizado sobre %d vertices de las bandas" % nsel)

        with Activated(ob):
            bpy.ops.object.vertex_group_normalize_all(
                group_select_mode='BONE_DEFORM', lock_active=False)

        after = abrupt_count(ob, arm_ob)
        print("   aristas abruptas DESPUES: %d  (%+d)" % (after, after - before))
        report.append({"object": name, "abrupt_before": before,
                       "abrupt_after": after, "joints": stats})

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "rebuild_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)

    if OUT:
        save(OUT)


main()
