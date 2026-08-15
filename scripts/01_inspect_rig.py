# -*- coding: utf-8 -*-
"""
01_inspect_rig.py  --  Auditoria READ-ONLY del rig y los pesos.

No modifica ni guarda nada. Vuelca un JSON con:
  - armature(s), huesos de deformacion y jerarquia
  - por cada malla skinneada: vertex groups vacios / no-deform / huerfanos,
    vertices sin peso, pesos sin normalizar, exceso de influencias, pesos basura
  - deteccion de transiciones abruptas entre huesos (por arista)
  - densidad de loops en la zona de flexion de cada articulacion
  - shape keys y drivers ya existentes

Uso:
  blender -b modelo-base.blend --python scripts/01_inspect_rig.py -- --out report.json
"""

import bpy
import sys
import json
import math
from collections import defaultdict
from mathutils import Vector

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

# Peso por debajo del cual una influencia es ruido y solo ensucia la deformacion.
NOISE_WEIGHT = 0.01
# Maximo de influencias por vertice que consideramos sano para un personaje.
MAX_INFLUENCES = 4
# Tolerancia al comprobar que la suma de pesos de un vertice es 1.0.
NORMALIZE_TOL = 1e-3
# Delta L1 entre los vectores de peso de los 2 vertices de una arista a partir
# del cual la transicion se considera abrupta (2.0 = cambio total de hueso).
ABRUPT_EDGE_DELTA = 0.75
# Semiancho de la banda de flexion, como fraccion de la longitud del hueso mas corto.
BEND_BAND_FRAC = 0.30
# Dos vertices caen en el mismo "loop" si su proyeccion sobre el eje del hueso
# difiere menos que esto (fraccion del ancho total de la banda).
LOOP_CLUSTER_FRAC = 0.08

# Patrones de nombre -> articulacion canonica. Cubre VRM/VRoid (J_Bip_*),
# Mixamo, Rigify y nomenclatura generica en ingles.
JOINT_PATTERNS = [
    ("shoulder", ["shoulder", "clavicle", "hombro"]),
    ("upperarm", ["upperarm", "upper_arm", "arm_l", "arm_r", "brazo"]),
    ("elbow",    ["lowerarm", "lower_arm", "forearm", "fore_arm", "antebrazo", "codo"]),
    ("wrist",    ["hand", "wrist", "mano", "muneca"]),
    ("hip",      ["upperleg", "upper_leg", "thigh", "muslo", "cadera"]),
    ("knee",     ["lowerleg", "lower_leg", "shin", "calf", "rodilla"]),
    ("ankle",    ["foot", "ankle", "tobillo", "pie"]),
    ("neck",     ["neck", "cuello"]),
    ("spine",    ["spine", "chest", "upperchest", "torso", "columna"]),
    ("toe",      ["toe", "dedo_pie"]),
    ("finger",   ["thumb", "index", "middle", "ring", "little", "pinky"]),
]


def classify_bone(name):
    """Devuelve la articulacion canonica de un hueso, o None."""
    low = name.lower()
    for joint, keys in JOINT_PATTERNS:
        for k in keys:
            if k in low:
                return joint
    return None


def side_of(name):
    """Lado L / R / C a partir del nombre del hueso."""
    low = name.lower()
    for tag, side in (("_l_", "L"), ("_r_", "R"), ("left", "L"), ("right", "R"),
                      (".l", "L"), (".r", "R"), ("_l", "L"), ("_r", "R")):
        if low.endswith(tag) or tag in low:
            return side
    return "C"


# --------------------------------------------------------------------------
# Recoleccion
# --------------------------------------------------------------------------

def collect_armatures():
    out = {}
    for ob in bpy.data.objects:
        if ob.type != 'ARMATURE':
            continue
        arm = ob.data
        bones = {}
        for b in arm.bones:
            bones[b.name] = {
                "deform": bool(b.use_deform),
                "parent": b.parent.name if b.parent else None,
                "head": list(b.head_local),
                "tail": list(b.tail_local),
                "length": b.length,
                "joint": classify_bone(b.name),
                "side": side_of(b.name),
                "connected": bool(b.use_connect),
            }
        out[ob.name] = {
            "bone_count": len(bones),
            "deform_bone_count": sum(1 for v in bones.values() if v["deform"]),
            "bones": bones,
        }
    return out


def skinned_meshes():
    """Mallas con modificador Armature -> (objeto, objeto_armature)."""
    res = []
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        for m in ob.modifiers:
            if m.type == 'ARMATURE' and m.object:
                res.append((ob, m.object))
                break
    return res


def analyze_weights(ob, arm_ob):
    """Estadisticas de pesos de una malla, y el mapa vert -> {grupo: peso}."""
    me = ob.data
    vgs = ob.vertex_groups
    gname = {g.index: g.name for g in vgs}

    deform_bones = {b.name for b in arm_ob.data.bones if b.use_deform}
    all_bones = {b.name for b in arm_ob.data.bones}

    used_groups = set()
    unweighted = []          # vertices sin ninguna influencia util
    unnormalized = []        # suma != 1
    over_influence = []      # > MAX_INFLUENCES influencias reales
    noise_hits = 0           # numero de influencias por debajo de NOISE_WEIGHT
    negative_or_over = 0     # pesos fuera de [0,1]

    wmap = []                # lista paralela a me.vertices: {gidx: weight}
    for v in me.vertices:
        w = {}
        for g in v.groups:
            if g.group not in gname:
                continue
            val = g.weight
            if val < 0.0 or val > 1.0:
                negative_or_over += 1
            if val <= NOISE_WEIGHT:
                noise_hits += 1
                continue
            # Solo cuentan como influencia real los grupos que mueven hueso.
            if gname[g.group] in deform_bones:
                w[g.group] = val
                used_groups.add(g.group)
        wmap.append(w)

        total = sum(w.values())
        if total <= 0.0:
            unweighted.append(v.index)
        else:
            if abs(total - 1.0) > NORMALIZE_TOL:
                unnormalized.append(v.index)
            if len(w) > MAX_INFLUENCES:
                over_influence.append(v.index)

    empty_groups = [gname[i] for i in gname if i not in used_groups]
    non_deform_groups = [n for n in gname.values()
                         if n in all_bones and n not in deform_bones]
    orphan_groups = [n for n in gname.values() if n not in all_bones]

    stats = {
        "object": ob.name,
        "armature": arm_ob.name,
        "vertices": len(me.vertices),
        "vertex_groups": len(vgs),
        "empty_groups": sorted(empty_groups),
        "non_deform_groups": sorted(non_deform_groups),
        "orphan_groups": sorted(orphan_groups),
        "unweighted_verts": len(unweighted),
        "unweighted_sample": unweighted[:20],
        "unnormalized_verts": len(unnormalized),
        "over_influence_verts": len(over_influence),
        "noise_influences": noise_hits,
        "out_of_range_weights": negative_or_over,
    }
    return stats, wmap, gname


def detect_abrupt(ob, wmap, gname):
    """
    Recorre las aristas y mide cuanto cambia el vector de pesos entre sus dos
    vertices. Un salto grande = la piel pasa de un hueso a otro en una sola
    arista, que es exactamente lo que produce el pellizco al rotar.
    Agrupa los resultados por par de huesos.
    """
    me = ob.data
    pair_hits = defaultdict(int)
    pair_max = defaultdict(float)
    worst = []

    for e in me.edges:
        a, b = e.vertices
        wa, wb = wmap[a], wmap[b]
        if not wa or not wb:
            continue
        keys = set(wa) | set(wb)
        delta = sum(abs(wa.get(k, 0.0) - wb.get(k, 0.0)) for k in keys)
        if delta < ABRUPT_EDGE_DELTA:
            continue
        # El par de huesos responsable: el dominante a cada lado.
        da = max(wa, key=wa.get)
        db = max(wb, key=wb.get)
        if da == db:
            continue
        pair = tuple(sorted((gname[da], gname[db])))
        pair_hits[pair] += 1
        pair_max[pair] = max(pair_max[pair], delta)
        worst.append((delta, pair, a, b))

    worst.sort(reverse=True, key=lambda x: x[0])
    return {
        "abrupt_edges": sum(pair_hits.values()),
        "by_bone_pair": sorted(
            [{"bones": list(k), "edges": v, "max_delta": round(pair_max[k], 3)}
             for k, v in pair_hits.items()],
            key=lambda d: -d["edges"]
        )[:40],
        "worst_examples": [
            {"delta": round(d, 3), "bones": list(p), "verts": [a, b]}
            for d, p, a, b in worst[:15]
        ],
    }


def loop_density(ob, arm_ob, wmap, gname):
    """
    Para cada articulacion (hueso hijo con padre deformante), cuenta cuantos
    edge loops caen dentro de la banda de flexion. Menos de 3 loops => la
    articulacion no tiene geometria suficiente para doblarse sin colapsar.
    """
    name2gidx = {n: i for i, n in gname.items()}
    arm = arm_ob.data
    mw = ob.matrix_world
    aw_inv = arm_ob.matrix_world.inverted()
    # Vertices de la malla en espacio de armature.
    co_arm = [aw_inv @ (mw @ v.co) for v in ob.data.vertices]

    results = []
    for b in arm.bones:
        if not b.use_deform or not b.parent or not b.parent.use_deform:
            continue
        joint = classify_bone(b.name)
        if joint in (None, "finger", "toe"):
            continue
        gi_child = name2gidx.get(b.name)
        gi_parent = name2gidx.get(b.parent.name)
        if gi_child is None or gi_parent is None:
            continue

        origin = Vector(b.head_local)
        axis = (Vector(b.tail_local) - origin)
        if axis.length < 1e-6:
            continue
        axis.normalize()

        band = BEND_BAND_FRAC * min(b.length, b.parent.length)
        if band < 1e-6:
            continue

        projs = []
        for vi, w in enumerate(wmap):
            if not w:
                continue
            share = w.get(gi_child, 0.0) + w.get(gi_parent, 0.0)
            if share < 0.5:
                continue
            t = (co_arm[vi] - origin).dot(axis)
            if -band <= t <= band:
                projs.append(t)

        if not projs:
            results.append({
                "bone": b.name, "parent": b.parent.name, "joint": joint,
                "side": side_of(b.name), "loops_in_bend": 0,
                "verts_in_band": 0, "band": round(band, 5),
                "note": "sin vertices con peso compartido en la banda",
            })
            continue

        projs.sort()
        tol = LOOP_CLUSTER_FRAC * (2.0 * band)
        loops = 1
        last = projs[0]
        for t in projs[1:]:
            if t - last > tol:
                loops += 1
            last = t

        results.append({
            "bone": b.name, "parent": b.parent.name, "joint": joint,
            "side": side_of(b.name), "loops_in_bend": loops,
            "verts_in_band": len(projs), "band": round(band, 5),
        })
    return results


def existing_shapekeys_and_drivers():
    out = {"shape_keys": {}, "drivers": []}
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or not ob.data.shape_keys:
            continue
        kb = ob.data.shape_keys.key_blocks
        out["shape_keys"][ob.name] = [k.name for k in kb]
        ad = ob.data.shape_keys.animation_data
        if ad:
            for d in ad.drivers:
                out["drivers"].append({
                    "object": ob.name,
                    "path": d.data_path,
                    "expr": d.driver.expression,
                    "type": d.driver.type,
                    "vars": [{"name": v.name, "type": v.type} for v in d.driver.variables],
                })
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_path = "rig_report.json"
    if "--out" in argv:
        out_path = argv[argv.index("--out") + 1]

    report = {
        "blend": bpy.data.filepath,
        "blender": bpy.app.version_string,
        "armatures": collect_armatures(),
        "meshes": [],
        "shapekeys_drivers": existing_shapekeys_and_drivers(),
    }

    for ob, arm_ob in skinned_meshes():
        stats, wmap, gname = analyze_weights(ob, arm_ob)
        stats["transitions"] = detect_abrupt(ob, wmap, gname)
        stats["joint_geometry"] = loop_density(ob, arm_ob, wmap, gname)
        report["meshes"].append(stats)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)

    # Resumen corto en consola.
    print("\n" + "=" * 70)
    print("RESUMEN INSPECCION")
    print("=" * 70)
    for a, d in report["armatures"].items():
        print("Armature %-24s huesos=%d deform=%d" % (a, d["bone_count"], d["deform_bone_count"]))
    for m in report["meshes"]:
        print("\nMalla %-28s verts=%-7d vgroups=%d" % (m["object"], m["vertices"], m["vertex_groups"]))
        print("   sin peso=%-6d sin normalizar=%-6d >4infl=%-6d ruido=%-6d" % (
            m["unweighted_verts"], m["unnormalized_verts"],
            m["over_influence_verts"], m["noise_influences"]))
        print("   grupos vacios=%-4d no-deform=%-4d huerfanos=%d" % (
            len(m["empty_groups"]), len(m["non_deform_groups"]), len(m["orphan_groups"])))
        print("   aristas abruptas=%d" % m["transitions"]["abrupt_edges"])
    print("\nJSON -> %s" % out_path)


main()
