# -*- coding: utf-8 -*-
"""Utilidades compartidas por los scripts de rigging."""

import bpy
import sys
from mathutils import Vector


# --------------------------------------------------------------------------
# Argumentos tras el "--"
# --------------------------------------------------------------------------

def argv():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default=None):
    a = argv()
    if name in a:
        i = a.index(name)
        if i + 1 < len(a) and not a[i + 1].startswith("--"):
            return a[i + 1]
        return True
    return default


def flag(name):
    return name in argv()


# --------------------------------------------------------------------------
# Seleccion / activacion segura de objetos (headless)
# --------------------------------------------------------------------------

class Activated(object):
    """
    Deja `ob` activo, seleccionado y visible; restaura el estado al salir.
    Si el objeto no esta enlazado a ninguna coleccion de la escena lo enlaza
    temporalmente (caso de objetos huerfanos como Face.001).
    """

    def __init__(self, ob):
        self.ob = ob
        self.linked_temp = False
        self.prev_hide_vp = None
        self.prev_hide_set = None

    def __enter__(self):
        ob = self.ob
        # Un objeto sin colecciones (huerfano, p.ej. Face.001) no es
        # accesible para los operadores: lo enlazamos temporalmente.
        if not ob.users_collection:
            bpy.context.scene.collection.objects.link(ob)
            self.linked_temp = True
        if ob.name not in bpy.context.view_layer.objects:
            bpy.context.view_layer.update()

        self.prev_hide_vp = ob.hide_viewport
        ob.hide_viewport = False
        try:
            self.prev_hide_set = ob.hide_get()
            ob.hide_set(False)
        except RuntimeError:
            self.prev_hide_set = None

        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        return ob

    def __exit__(self, *exc):
        ob = self.ob
        try:
            if bpy.context.object and bpy.context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass
        try:
            ob.select_set(False)
        except RuntimeError:
            pass
        if self.prev_hide_set is not None:
            try:
                ob.hide_set(self.prev_hide_set)
            except RuntimeError:
                pass
        ob.hide_viewport = self.prev_hide_vp
        if self.linked_temp:
            bpy.context.scene.collection.objects.unlink(ob)
        return False


# --------------------------------------------------------------------------
# Mallas skinneadas y grupos protegidos
# --------------------------------------------------------------------------

def skinned_meshes():
    """[(objeto_malla, objeto_armature)] para toda malla con modificador Armature."""
    out = []
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        for m in ob.modifiers:
            if m.type == 'ARMATURE' and m.object:
                out.append((ob, m.object))
                break
    return out


def _sweep_vgroup_props(holder, keep):
    """Recoge toda propiedad string cuyo identificador mencione vertex_group."""
    if holder is None:
        return
    try:
        props = holder.bl_rna.properties
    except AttributeError:
        return
    for p in props:
        if p.type != 'STRING' or 'vertex_group' not in p.identifier:
            continue
        val = getattr(holder, p.identifier, None)
        if isinstance(val, str) and val:
            keep.add(val)


def protected_groups(ob):
    """
    Vertex groups que NO se pueden borrar porque algo los consume:
    modificadores (Mask, Solidify, Cloth pin...), fisicas, particulas y
    shape keys. En este modelo aqui vive 'ocultar_ropa', que alimenta el
    modificador Mask del Body: borrarlo destruiria el modelo.
    """
    keep = set()
    for m in ob.modifiers:
        _sweep_vgroup_props(m, keep)
        for sub in ('settings', 'collision_settings', 'effector_weights',
                    'brush', 'canvas_settings'):
            _sweep_vgroup_props(getattr(m, sub, None), keep)
    _sweep_vgroup_props(getattr(ob, 'soft_body', None), keep)
    for psys in getattr(ob, 'particle_systems', []):
        _sweep_vgroup_props(psys, keep)
    if ob.data.shape_keys:
        for kb in ob.data.shape_keys.key_blocks:
            if kb.vertex_group:
                keep.add(kb.vertex_group)
    return keep


# --------------------------------------------------------------------------
# Geometria de articulaciones
# --------------------------------------------------------------------------

def bone_space_coords(ob, arm_ob):
    """Vertices de `ob` expresados en el espacio local de la armature."""
    mw = ob.matrix_world
    inv = arm_ob.matrix_world.inverted()
    return [inv @ (mw @ v.co) for v in ob.data.vertices]


def weight_map(ob, min_w=0.0):
    """[{group_index: weight}] paralelo a ob.data.vertices."""
    return [{g.group: g.weight for g in v.groups if g.weight > min_w}
            for v in ob.data.vertices]


def joint_frame(arm_ob, bone_name):
    """(origen, eje_unitario) de la articulacion, en espacio de armature."""
    b = arm_ob.data.bones[bone_name]
    o = Vector(b.head_local)
    ax = Vector(b.tail_local) - o
    if ax.length < 1e-9:
        return o, Vector((0.0, 1.0, 0.0))
    return o, ax.normalized()


def joint_radius(ob, arm_ob, bone_name, coords=None, wmap=None):
    """
    Radio del miembro medido en la propia articulacion: distancia media al eje
    del hueso de los vertices mas cercanos al plano de la articulacion.
    Sirve para dimensionar la 'zona de flexion' (~1 radio a cada lado).
    """
    b = arm_ob.data.bones[bone_name]
    if not b.parent:
        return 0.0
    name2gi = {g.name: g.index for g in ob.vertex_groups}
    gi_c, gi_p = name2gi.get(bone_name), name2gi.get(b.parent.name)
    if gi_c is None or gi_p is None:
        return 0.0
    if coords is None:
        coords = bone_space_coords(ob, arm_ob)
    if wmap is None:
        wmap = weight_map(ob, 0.01)

    o, ax = joint_frame(arm_ob, bone_name)
    near = []
    for vi, w in enumerate(wmap):
        if w.get(gi_c, 0.0) + w.get(gi_p, 0.0) < 0.5:
            continue
        d = coords[vi] - o
        t = d.dot(ax)
        near.append((abs(t), (d - t * ax).length))
    if not near:
        return 0.0
    near.sort()
    k = max(4, len(near) // 10)
    r = sum(rr for _, rr in near[:k]) / k
    # Tope de seguridad: en articulaciones cuyo eje no sigue al miembro (el pie
    # apunta hacia delante, no hacia abajo) la media se dispara y la banda
    # acabaria abarcando medio cuerpo.
    return min(r, 0.5 * min(b.length, b.parent.length))


def joint_region(ob, arm_ob, bone_name, band, coords=None, wmap=None,
                 min_share=0.25, sphere_scale=1.6):
    """
    {indice_vertice: t} de los vertices que pertenecen de verdad a la
    articulacion. Un vertice entra si cumple LAS TRES condiciones:

      1. |t| <= band  a lo largo del eje del hueso  (rebanada)
      2. distancia al centro de la articulacion <= band * sphere_scale (esfera)
      3. al menos `min_share` de su peso repartido entre el hueso y su padre

    Las tres hacen falta. Solo con (1) la 'banda' es una rebanada infinita que
    cruza el modelo entero. Anadiendo (3) sigue sin bastar: en el tobillo el eje
    del pie apunta hacia delante, la espinilla queda casi paralela al plano de
    corte y toda la pierna (que pesa sobre LowerLeg, el padre) se colaba hasta
    la rodilla. El corte esferico (2) es el que acota la region de verdad.
    """
    b = arm_ob.data.bones[bone_name]
    if not b.parent:
        return {}
    name2gi = {g.name: g.index for g in ob.vertex_groups}
    gi_c, gi_p = name2gi.get(bone_name), name2gi.get(b.parent.name)
    if gi_c is None or gi_p is None:
        return {}
    if coords is None:
        coords = bone_space_coords(ob, arm_ob)
    if wmap is None:
        wmap = weight_map(ob, 0.0)

    o, ax = joint_frame(arm_ob, bone_name)
    rmax = band * sphere_scale
    out = {}
    for vi, c in enumerate(coords):
        w = wmap[vi]
        if w.get(gi_c, 0.0) + w.get(gi_p, 0.0) < min_share:
            continue
        d = c - o
        if d.length > rmax:
            continue
        t = d.dot(ax)
        if -band <= t <= band:
            out[vi] = t
    return out


# --------------------------------------------------------------------------
# Definicion de articulaciones del rig VRM (J_Bip_*)
# --------------------------------------------------------------------------
# clave -> (hueso_hijo, etiqueta, angulo_objetivo_grados, tipo)
#   tipo 'hinge' = bisagra (codo, rodilla...), 'ball' = rotula (hombro, cadera)

JOINTS = [
    ("codo_L",      "J_Bip_L_LowerArm", "codo izquierdo",        120.0, "hinge"),
    ("codo_R",      "J_Bip_R_LowerArm", "codo derecho",          120.0, "hinge"),
    ("rodilla_L",   "J_Bip_L_LowerLeg", "rodilla izquierda",     120.0, "hinge"),
    ("rodilla_R",   "J_Bip_R_LowerLeg", "rodilla derecha",       120.0, "hinge"),
    ("axila_L",     "J_Bip_L_UpperArm", "hombro/axila izquierda", 90.0, "ball"),
    ("axila_R",     "J_Bip_R_UpperArm", "hombro/axila derecha",   90.0, "ball"),
    ("cadera_L",    "J_Bip_L_UpperLeg", "cadera izquierda",       90.0, "ball"),
    ("cadera_R",    "J_Bip_R_UpperLeg", "cadera derecha",         90.0, "ball"),
    ("muneca_L",    "J_Bip_L_Hand",     "muneca izquierda",       60.0, "hinge"),
    ("muneca_R",    "J_Bip_R_Hand",     "muneca derecha",         60.0, "hinge"),
    ("tobillo_L",   "J_Bip_L_Foot",     "tobillo izquierdo",      45.0, "hinge"),
    ("tobillo_R",   "J_Bip_R_Foot",     "tobillo derecho",        45.0, "hinge"),
    ("hombro_L",    "J_Bip_L_Shoulder", "clavicula izquierda",    30.0, "ball"),
    ("hombro_R",    "J_Bip_R_Shoulder", "clavicula derecha",      30.0, "ball"),
    ("cuello",      "J_Bip_C_Neck",     "cuello",                 45.0, "ball"),
]

# Falanges: tienen recorrido amplio y salen mal del export de VRoid, pero no
# justifican un shape key correctivo cada una. Solo se usan para recalcular
# pesos (script 03), no para correctivos (script 04).
FINGER_JOINTS = []
for _side in ("L", "R"):
    for _f in ("Thumb", "Index", "Middle", "Ring", "Little"):
        for _i in (1, 2, 3):
            FINGER_JOINTS.append((
                "%s%d_%s" % (_f.lower(), _i, _side),
                "J_Bip_%s_%s%d" % (_side, _f, _i),
                "%s %d %s" % (_f, _i, _side),
                80.0, "hinge"))

# Correctivos de torsion: este rig VRM no tiene huesos twist, asi que el
# antebrazo/muneca hace 'candy wrapper' al girar la mano.
TWISTS = [
    ("antebrazo_twist_pos_L", "J_Bip_L_Hand", "antebrazo izq. pronacion",   90.0),
    ("antebrazo_twist_neg_L", "J_Bip_L_Hand", "antebrazo izq. supinacion", -90.0),
    ("antebrazo_twist_pos_R", "J_Bip_R_Hand", "antebrazo der. pronacion",   90.0),
    ("antebrazo_twist_neg_R", "J_Bip_R_Hand", "antebrazo der. supinacion", -90.0),
]


def save(path):
    bpy.ops.wm.save_as_mainfile(filepath=bpy.path.abspath(path))
    print("\n>> guardado: %s" % path)
