# Scripts de rigging — deformación en articulaciones

Herramientas en `bpy` para auditar y arreglar la deformación de la malla del
modelo VRM (`modelo-base.blend`, rig VRoid `J_Bip_*`, 118 huesos).

Probado con **Blender 4.5.1 LTS**. Ningún script sobreescribe el original:
todos escriben donde diga `--out`.

---

## Los scripts

| script | qué hace | modifica |
|---|---|---|
| `01_inspect_rig.py` | Auditoría read-only: grupos vacíos/huérfanos, pesos sin normalizar, exceso de influencias, transiciones abruptas, densidad de loops | no |
| `02_clean_weights.py` | Limpia y normaliza vertex groups de las 71 mallas skinneadas | sí |
| `03_rebuild_joint_weights.py` | Bone Heat **acotado** a las articulaciones que elijas | sí |
| `04_corrective_drivers.py` | Crea shape keys correctivos + drivers por ángulo | sí |
| `05_verify_deformation.py` | Dobla de verdad cada articulación y mide el pellizco | no |

`_common.py` es la librería compartida (regiones de articulación, tabla de
juntas, activación segura de objetos headless).

## Cadena completa

```powershell
$B = "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

& $B -b "modelo-base.blend"     --python scripts\02_clean_weights.py -- --out "modelo-base-fix.blend"
& $B -b "modelo-base-fix.blend" --python scripts\03_rebuild_joint_weights.py -- --out "modelo-base-fix.blend" --joints "codo_L,codo_R,tobillo_L,tobillo_R,cuello"
& $B -b "modelo-base-fix.blend" --python scripts\04_corrective_drivers.py -- --out "modelo-base-fix.blend"
& $B -b "modelo-base-fix.blend" --python scripts\05_verify_deformation.py -- --angle 100
```

---

## Por qué `--joints` no es "todas"

`03` se ejecuta **solo sobre el conjunto medido como ganador**. Bone Heat no es
mejor que los pesos originales en todas partes: VRoid trae la axila y la cadera
afinadas a mano y el algoritmo geodésico las empeora.

Medido con `05_verify_deformation.py` a 100° (caras que pierden >70% de área):

| articulación | pesos limpios | + Bone Heat | |
|---|---|---|---|
| codo L / R | 6 / 8 | **0 / 0** | ✅ aplicado |
| cuello | 14 | **7** | ✅ aplicado |
| tobillo L / R | 5 / 5 | **3 / 3** | ✅ aplicado |
| hombro L / R | 10 / 6 | 10 / 6 | ➖ sin cambio |
| cadera L / R | 2 / 0 | 2 / 0 | ➖ sin cambio |
| rodilla L / R | 7 / 5 | 8 / 9 | ❌ descartado |
| axila L / R | 2 / 2 | 12 / 8 | ❌ descartado |
| muñeca L / R | 4 / 5 | 7 / 5 | ❌ descartado |
| falanges (×30) | 35 | 40 | ❌ descartado |

**Total: 81 → 56 caras colapsadas, sin empeorar ninguna junta.**

> Ojo con la métrica de "aristas abruptas" de `01`: baja de 386 a 134 aplicando
> Bone Heat en todo, pero la deformación real se queda igual o peor. Una
> transición nítida no es un defecto si está bien colocada. Fíate de `05`.

---

## Cómo esculpir los correctivos

`04` deja 19 shape keys vacíos (sin efecto hasta que los toques) ya cableados a
drivers, y activa en el modificador Armature del Body *Display in Edit Mode* +
*On Cage*, que es lo que permite editar viendo la malla ya deformada.

Los rangos de los drivers están puestos **justo en el ángulo de escultura**, así
que al posar el hueso el correctivo vale exactamente 1.0 y ves lo que haces:

1. Armature → Pose Mode → rota el hueso al ángulo de la tabla de abajo.
2. Selecciona `Body` → Object Data Properties → Shape Keys → elige el `corr_*`.
3. Edit Mode. Ves la malla deformada; mueve vértices hasta que deje de pellizcar.
4. Object Mode. Listo — el driver lo activa y desactiva solo.

| shape key | hueso | pose de escultura |
|---|---|---|
| `corr_codo_L/R` | `J_Bip_*_LowerArm` | flexión 120° |
| `corr_rodilla_L/R` | `J_Bip_*_LowerLeg` | flexión 120° |
| `corr_axila_L/R` | `J_Bip_*_UpperArm` | brazo arriba 90° |
| `corr_cadera_L/R` | `J_Bip_*_UpperLeg` | pierna al frente 90° |
| `corr_muneca_L/R` | `J_Bip_*_Hand` | 60° |
| `corr_tobillo_L/R` | `J_Bip_*_Foot` | 45° |
| `corr_hombro_L/R` | `J_Bip_*_Shoulder` | 30° |
| `corr_cuello` | `J_Bip_C_Neck` | 45° |
| `corr_antebrazo_twist_pos/neg_L/R` | `J_Bip_*_Hand` | giro ±90° |

### Cómo mide el ángulo el driver

El eje de flexión de este rig **no cae sobre un eje local limpio** (en el codo,
ROT_X y ROT_Z dan exactamente el mismo resultado: la bisagra está a ~45° de los
ejes del hueso). Por eso el driver no usa un eje sino la magnitud del swing:

```
sqrt(sx*sx + sz*sz)     sx, sz = ROT_X / ROT_Z, LOCAL_SPACE, SWING_TWIST_Y
```

En espacio local el reposo es 0 sea cual sea la orientación del padre, y la
magnitud mide el doblez venga por donde venga. Se descartó `ROTATION_DIFF`
porque en cadera y tobillo marca ya 180° en reposo y se satura.

La respuesta es un F-curve de dos keyframes con extrapolación constante, no una
expresión de rango: es una expresión *simple*, así que **no hace falta activar
Auto-Run Python Scripts**, y puedes retocar la curva en el Graph Editor.

Verificado sobre el codo derecho: 0°→0.000, 30°→0.250, 60°→0.500, 90°→0.750,
120°→1.000, 150°→1.000.

---

## Grupos protegidos

`02` **nunca** borra un vertex group que consuma un modificador o una física.
En este archivo eso salva a **`ocultar_ropa`** (4790 vértices), que alimenta el
modificador Mask del Body para esconder la piel bajo la ropa. Es un grupo
huérfano — no existe ningún hueso con ese nombre — así que una limpieza ingenua
"borra los grupos que no son huesos" lo destruiría y dejaría la piel atravesando
la ropa.

La detección es genérica: barre toda propiedad string de los modificadores y de
sus `settings` / `collision_settings` cuyo identificador contenga `vertex_group`,
más soft body, sistemas de partículas y los `vertex_group` de shape keys.
