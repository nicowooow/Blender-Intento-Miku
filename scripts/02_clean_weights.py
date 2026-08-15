# -*- coding: utf-8 -*-
"""
02_clean_weights.py  --  Limpieza y normalizacion de vertex groups / pesos.

Por cada malla skinneada:
  1. Borra vertex groups HUERFANOS (no corresponden a ningun hueso) salvo los
     que consume algun modificador o sistema de fisicas.  << 'ocultar_ropa'
     alimenta el modificador Mask del Body y esta protegido explicitamente. >>
  2. Borra vertex groups de hueso que estan VACIOS (0 influencias reales).
  3. Elimina influencias residuales por debajo de --noise (default 0.01),
     sin dejar nunca un vertice sin peso (keep_single).
  4. Limita a --max-infl influencias por vertice (default 4).
  5. Normaliza todos los pesos de deformacion a suma 1.0.
  6. Informa de los vertices que sigan sin peso.

Solo toca los grupos de huesos de DEFORMACION (BONE_DEFORM); los grupos de
mascara, pin de tela, etc. quedan intactos.

Uso:
  blender -b modelo-base.blend --python scripts/02_clean_weights.py -- \
      --out modelo-base-fix.blend [--noise 0.01] [--max-infl 4] [--dry-run]
"""

import bpy
import os
import sys
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _common import (argv, arg, flag, Activated, skinned_meshes,
                     protected_groups, save)

NOISE = float(arg("--noise", 0.01))
MAX_INFL = int(arg("--max-infl", 4))
DRY = flag("--dry-run")
OUT = arg("--out")


def group_usage(ob):
    """{group_index: numero de vertices con peso > 0} para la malla."""
    used = {}
    for v in ob.data.vertices:
        for g in v.groups:
            if g.weight > 0.0:
                used[g.group] = used.get(g.group, 0) + 1
    return used


def clean_mesh(ob, arm_ob, report):
    bones = {b.name for b in arm_ob.data.bones}
    deform = {b.name for b in arm_ob.data.bones if b.use_deform}
    keep = protected_groups(ob)

    used = group_usage(ob)
    orphan, empty = [], []
    for g in ob.vertex_groups:
        if g.name in keep:
            continue
        if g.name not in bones:
            orphan.append(g.name)
        elif used.get(g.index, 0) == 0 and g.name in deform:
            empty.append(g.name)

    entry = {
        "object": ob.name,
        "protected": sorted(keep),
        "removed_orphan": sorted(orphan),
        "removed_empty": len(empty),
        "removed_empty_names": sorted(empty),
    }

    if not DRY:
        for name in orphan + empty:
            vg = ob.vertex_groups.get(name)
            if vg:
                ob.vertex_groups.remove(vg)

    # --- operadores de limpieza, solo sobre grupos de deformacion ---
    if not DRY:
        with Activated(ob):
            bpy.ops.object.vertex_group_clean(
                group_select_mode='BONE_DEFORM', limit=NOISE, keep_single=True)
            bpy.ops.object.vertex_group_limit_total(
                group_select_mode='BONE_DEFORM', limit=MAX_INFL)
            bpy.ops.object.vertex_group_normalize_all(
                group_select_mode='BONE_DEFORM', lock_active=False)

    # --- verificacion posterior ---
    gname = {g.index: g.name for g in ob.vertex_groups}
    unweighted, unnorm, over = 0, 0, 0
    for v in ob.data.vertices:
        w = [g.weight for g in v.groups
             if gname.get(g.group) in deform and g.weight > 0.0]
        s = sum(w)
        if s <= 1e-6:
            unweighted += 1
        else:
            if abs(s - 1.0) > 1e-3:
                unnorm += 1
            if len(w) > MAX_INFL:
                over += 1
    entry.update({"unweighted_after": unweighted,
                  "unnormalized_after": unnorm,
                  "over_influence_after": over,
                  "vertex_groups_after": len(ob.vertex_groups)})
    report.append(entry)
    return entry


def main():
    print("=" * 74)
    print("LIMPIEZA DE PESOS  (noise=%.3f  max_infl=%d  dry_run=%s)" % (NOISE, MAX_INFL, DRY))
    print("=" * 74)

    report = []
    meshes = skinned_meshes()
    for ob, arm_ob in meshes:
        e = clean_mesh(ob, arm_ob, report)
        print("%-22s huerfanos=%-3d vacios=%-4d  ->  vg=%-4d sin_peso=%-4d "
              "sin_norm=%-4d >%dinfl=%d" % (
                  ob.name, len(e["removed_orphan"]), e["removed_empty"],
                  e["vertex_groups_after"], e["unweighted_after"],
                  e["unnormalized_after"], MAX_INFL, e["over_influence_after"]))

    prot = sorted({p for e in report for p in e["protected"]})
    tot_orph = sum(len(e["removed_orphan"]) for e in report)
    tot_empty = sum(e["removed_empty"] for e in report)
    print("-" * 74)
    print("mallas procesadas : %d" % len(report))
    print("grupos huerfanos borrados : %d" % tot_orph)
    print("grupos vacios borrados    : %d" % tot_empty)
    print("grupos PROTEGIDOS (no tocados) : %s" % (prot or "-"))
    print("vertices sin peso restantes    : %d" % sum(e["unweighted_after"] for e in report))
    print("vertices sin normalizar        : %d" % sum(e["unnormalized_after"] for e in report))

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "clean_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)

    if OUT and not DRY:
        save(OUT)
    elif DRY:
        print("\n(dry-run: no se guardo nada)")


main()
