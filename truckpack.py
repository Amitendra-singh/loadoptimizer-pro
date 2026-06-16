"""
truckpack.py - Truck load-optimization visualizer built on scene_kit.

Pipeline:
    product list (dims, qty)  ->  pack()  ->  placements + utilization %
                                                  |
                                  build_loaded_truck() -> photoreal Cycles render

The packer is a deterministic 3D layered shelf heuristic (no overlaps, axis-aligned).
Swap in any external optimizer: feed its placements straight to `place_boxes()`.
"""

import bpy
import os
import sys
import math
import importlib
import random

KIT_DIR = "/Users/amitendrasinghthenua/blender-mcp/photoreal"
if KIT_DIR not in sys.path:
    sys.path.insert(0, KIT_DIR)
import scene_kit
import truckspec
importlib.reload(scene_kit)
importlib.reload(truckspec)

# Free CC0 warehouse HDRI (Poly Haven) - reproducible lighting without re-downloading
_HDRI_4K = os.path.join(KIT_DIR, "assets", "empty_warehouse_01_4k.hdr")
_HDRI_2K = os.path.join(KIT_DIR, "assets", "empty_warehouse_01_2k.hdr")
HDRI_PATH = _HDRI_4K if os.path.exists(_HDRI_4K) else _HDRI_2K
HDRI_ROT = 132.0   # rotate warehouse so a clean bay (not a pillar) sits behind the truck


# --------------------------------------------------------------------------- #
#  Default truck + product catalog (all metres)
# --------------------------------------------------------------------------- #
# Interior cargo volume of a mid-size box truck (~ length x width x height)
DEFAULT_TRUCK = (5.0, 2.30, 2.20)

# label -> (l, w, h, base_color, count).  Colors are sRGB-ish base colors.
DEFAULT_CATALOG = [
    dict(label="appliance", l=0.62, w=0.60, h=0.82, color=(0.34, 0.19, 0.09), count=20),
    dict(label="medium",    l=0.52, w=0.40, h=0.40, color=(0.50, 0.33, 0.17), count=50),
    dict(label="small",     l=0.40, w=0.30, h=0.30, color=(0.40, 0.25, 0.13), count=70),
    dict(label="long",      l=1.00, w=0.32, h=0.30, color=(0.30, 0.18, 0.09), count=24),
    dict(label="retail",    l=0.44, w=0.40, h=0.50, color=(0.72, 0.70, 0.66), count=34),
    dict(label="blue",      l=0.50, w=0.50, h=0.34, color=(0.10, 0.24, 0.46), count=28),
]


# --------------------------------------------------------------------------- #
#  Packing engine  (3D layered shelf, first-fit-decreasing)
# --------------------------------------------------------------------------- #
def pack(container, catalog, gap=0.006):
    """Return (placements, leftovers, stats).

    placement = dict(x,y,z,l,w,h,color,label)  with (x,y,z) the min corner.
    """
    L, W, H = container
    boxes = []
    for it in catalog:
        for _ in range(it["count"]):
            boxes.append({k: it[k] for k in ("l", "w", "h", "color", "label")})
    # largest / tallest first => denser, cleaner layers
    boxes.sort(key=lambda b: (b["h"], b["l"] * b["w"]), reverse=True)

    placements = []
    z = 0.0
    while boxes and z <= H - 1e-6:
        layer_h = 0.0
        x = 0.0
        while x <= L - 1e-6 and boxes:
            shelf_l = 0.0
            y = 0.0
            placed_in_shelf = False
            i = 0
            while i < len(boxes) and y <= W - 1e-6:
                b = boxes[i]
                if (b["l"] <= L - x + 1e-9 and
                        b["w"] <= W - y + 1e-9 and
                        b["h"] <= H - z + 1e-9):
                    placements.append(dict(x=x, y=y, z=z, **b))
                    y += b["w"] + gap
                    shelf_l = max(shelf_l, b["l"])
                    layer_h = max(layer_h, b["h"])
                    boxes.pop(i)
                    placed_in_shelf = True
                else:
                    i += 1
            if not placed_in_shelf:
                break
            x += shelf_l + gap
        if layer_h <= 0.0:
            break
        z += layer_h + gap

    box_vol = sum(p["l"] * p["w"] * p["h"] for p in placements)
    truck_vol = L * W * H
    stats = dict(
        placed=len(placements),
        leftover=len(boxes),
        utilization=round(100.0 * box_vol / truck_vol, 1),
        truck_vol=round(truck_vol, 2),
        used_vol=round(box_vol, 2),
    )
    return placements, boxes, stats


_PACK_STRATEGIES = {
    "volume":    lambda b: (b["l"] * b["w"] * b["h"], b["h"]),
    "height":    lambda b: (b["h"], b["l"] * b["w"]),
    "footprint": lambda b: (b["l"] * b["w"], b["h"]),
    "longest":   lambda b: (max(b["l"], b["w"], b["h"]), b["l"] * b["w"]),
}


def pack_best(container, catalog, strategies=("volume", "height", "footprint", "longest")):
    """Try several loading strategies and keep the densest result (commercial packers
    do this). Returns (placements, leftovers, stats) for the best fill."""
    best = None
    for s in strategies:
        pl, lo, st = pack_skyline(container, catalog, strategy=s)
        if best is None or st["used_vol"] > best[2]["used_vol"]:
            best = (pl, lo, st)
    return best


def pack_skyline(container, catalog, cell=0.04, flat_tol=0.02, allow_rotate=True,
                 strategy="volume"):
    """3D height-map (skyline) packer: bottom-left-fill with stacking + yaw rotation.

    Boxes rest on whatever is below them and fill gaps, so it packs far tighter
    than simple layers. Returns (placements, leftovers, stats), same schema as pack().
    """
    import numpy as np
    L, W, H = container
    nx = max(1, int(round(L / cell)))
    ny = max(1, int(round(W / cell)))
    cx, cy = L / nx, W / ny                      # exact cell sizes

    items = []
    for it in catalog:
        for _ in range(it["count"]):
            items.append({k: it[k] for k in ("l", "w", "h", "color", "label")})
    # ordering strategy (pack_best tries several and keeps the densest)
    items.sort(key=_PACK_STRATEGIES.get(strategy, _PACK_STRATEGIES["volume"]), reverse=True)

    grid = np.zeros((nx, ny), dtype=float)
    # tiny positional bias so ties resolve to front-left (tidy, deterministic)
    bias_x = (np.arange(nx)[:, None] * 1e-4)
    bias_y = (np.arange(ny)[None, :] * 1e-5)

    placements, leftovers = [], []
    for b in items:
        best = None  # (support_z, score, i, j, lx, wy, fx, fy)
        orients = [(b["l"], b["w"])]
        if allow_rotate and abs(b["l"] - b["w"]) > 1e-6:
            orients.append((b["w"], b["l"]))
        for (lx, wy) in orients:
            fx = max(1, int(np.ceil(lx / cx - 1e-9)))
            fy = max(1, int(np.ceil(wy / cy - 1e-9)))
            if fx > nx or fy > ny:
                continue
            ox, oy = nx - fx + 1, ny - fy + 1
            M = np.full((ox, oy), -1.0)
            m = np.full((ox, oy), 1e9)
            for dx in range(fx):
                for dy in range(fy):
                    win = grid[dx:dx + ox, dy:dy + oy]
                    M = np.maximum(M, win)
                    m = np.minimum(m, win)
            valid = (M - m <= flat_tol) & (M + b["h"] <= H + 1e-9)
            if not valid.any():
                continue
            score = np.where(valid, M + bias_x[:ox] + bias_y[:, :oy], np.inf)
            idx = int(np.argmin(score))
            i, j = divmod(idx, oy)
            sup = float(M[i, j])
            if best is None or sup < best[0] - 1e-9:
                best = (sup, score[i, j], i, j, lx, wy, fx, fy)
        if best is None:
            leftovers.append(b)
            continue
        sup, _, i, j, lx, wy, fx, fy = best
        placements.append(dict(x=i * cx, y=j * cy, z=sup,
                               l=lx, w=wy, h=b["h"],
                               color=b["color"], label=b["label"]))
        grid[i:i + fx, j:j + fy] = sup + b["h"]

    box_vol = sum(p["l"] * p["w"] * p["h"] for p in placements)
    truck_vol = L * W * H
    requested, placed_n = {}, {}
    for it in catalog:
        requested[it["label"]] = requested.get(it["label"], 0) + it["count"]
    for p in placements:
        placed_n[p["label"]] = placed_n.get(p["label"], 0) + 1
    breakdown = [dict(label=lbl, requested=requested[lbl],
                      placed=placed_n.get(lbl, 0),
                      left=requested[lbl] - placed_n.get(lbl, 0))
                 for lbl in requested]
    stats = dict(placed=len(placements), leftover=len(leftovers),
                 utilization=round(100.0 * box_vol / truck_vol, 1),
                 truck_vol=round(truck_vol, 2), used_vol=round(box_vol, 2),
                 breakdown=breakdown)
    return placements, leftovers, stats


def verify_no_overlap(placements):
    """AABB pairwise overlap check - returns list of colliding index pairs."""
    bad = []
    n = len(placements)
    for i in range(n):
        a = placements[i]
        for j in range(i + 1, n):
            b = placements[j]
            if (a["x"] < b["x"] + b["l"] - 1e-6 and a["x"] + a["l"] > b["x"] + 1e-6 and
                    a["y"] < b["y"] + b["w"] - 1e-6 and a["y"] + a["w"] > b["y"] + 1e-6 and
                    a["z"] < b["z"] + b["h"] - 1e-6 and a["z"] + a["h"] > b["z"] + 1e-6):
                bad.append((i, j))
    return bad


# --------------------------------------------------------------------------- #
#  Geometry
# --------------------------------------------------------------------------- #
def _box(name, xmin, xmax, ymin, ymax, zmin, zmax, material=None, bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=((xmin + xmax) / 2,
                                              (ymin + ymax) / 2,
                                              (zmin + zmax) / 2))
    o = bpy.context.active_object
    o.name = name
    o.scale = ((xmax - xmin) / 2, (ymax - ymin) / 2, (zmax - zmin) / 2)
    bpy.ops.object.transform_apply(scale=True)   # bake scale -> real world units
    if material:
        scene_kit.assign_material(o, material)
    if bevel > 0:
        m = o.modifiers.new("Bevel", "BEVEL")
        m.width = bevel
        m.segments = 2
        m.limit_method = "ANGLE"
    return o


def ribbed_metal_material(name, base_color=(0.52, 0.53, 0.55), rough=0.4,
                          metallic=0.7, rib_scale=42.0, strength=0.3):
    """Brushed metal with corrugated ribs - reads as a box-truck wall liner."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    scene_kit._set_input(bsdf, "Base Color", base_color)
    scene_kit._set_input(bsdf, "Metallic", metallic)
    scene_kit._set_input(bsdf, "Roughness", rough)
    coord = nodes.new("ShaderNodeTexCoord")
    wave = nodes.new("ShaderNodeTexWave")
    wave.wave_type = 'BANDS'
    try:
        wave.bands_direction = 'X'
    except Exception:
        pass
    wave.inputs["Scale"].default_value = rib_scale
    links.new(coord.outputs["Object"], wave.inputs["Vector"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    links.new(wave.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def build_truck_shell(container, thickness=0.06):
    """Five-sided cargo box (back open toward +X) with PBR interior surfaces."""
    L, W, H = container
    wall = ribbed_metal_material("TruckWall")
    floor = scene_kit.make_material("TruckFloor", base_color=(0.19, 0.13, 0.08),
                                    roughness=0.5, metallic=0.0)     # plywood deck
    ceil = scene_kit.make_material("TruckCeiling", base_color=(0.80, 0.80, 0.80),
                                   roughness=0.5, metallic=0.0,
                                   emission_color=(1.0, 0.98, 0.95),
                                   emission_strength=0.0)
    coll = bpy.data.collections.new("Truck")
    bpy.context.scene.collection.children.link(coll)

    parts = [
        ("Floor",   -thickness, L,  0, W,  -thickness, 0,            floor),
        ("Ceiling", -thickness, L,  0, W,  H,          H + thickness, ceil),
        ("FrontWall", -thickness, 0, 0, W,  0,         H,             wall),
        ("LeftWall",  0, L, -thickness, 0, 0,          H,             wall),
        ("RightWall", 0, L,  W, W + thickness, 0,      H,             wall),
    ]
    for name, x0, x1, y0, y1, z0, z1, mat in parts:
        o = _box(name, x0, x1, y0, y1, z0, z1, material=mat)
        for c in o.users_collection:
            c.objects.unlink(o)
        coll.objects.link(o)
    return coll


def cardboard_material(name, base_color=(0.45, 0.30, 0.17), rough=0.85, tape=True):
    """Procedural shipping carton: kraft mottle + paper-fibre bump + a wrapped
    packing-tape band (the instant 'this is a box' cue)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    coord = nodes.new("ShaderNodeTexCoord")

    # large-scale tonal mottle for the carton colour
    nz = nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 5.5
    links.new(coord.outputs["Object"], nz.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    c1 = tuple(min(1.0, max(0.0, c * 0.80)) for c in base_color)
    c2 = tuple(min(1.0, max(0.0, c * 1.15)) for c in base_color)
    ramp.color_ramp.elements[0].color = (*c1, 1.0)
    ramp.color_ramp.elements[1].color = (*c2, 1.0)
    links.new(nz.outputs["Fac"], ramp.inputs["Fac"])
    color_out = ramp.outputs["Color"]

    if tape:
        # tape band: a stripe near the girth (Generated coords => normalised per box)
        sep = nodes.new("ShaderNodeSeparateXYZ")
        links.new(coord.outputs["Generated"], sep.inputs["Vector"])
        sub = nodes.new("ShaderNodeMath"); sub.operation = 'SUBTRACT'
        sub.inputs[1].default_value = 0.5
        links.new(sep.outputs["X"], sub.inputs[0])
        ab = nodes.new("ShaderNodeMath"); ab.operation = 'ABSOLUTE'
        links.new(sub.outputs["Value"], ab.inputs[0])
        band = nodes.new("ShaderNodeMath"); band.operation = 'LESS_THAN'
        band.inputs[1].default_value = 0.07
        links.new(ab.outputs["Value"], band.inputs[0])
        mask = band.outputs["Value"]
        # colour: kraft vs beige tape
        cmix = nodes.new("ShaderNodeMixRGB")
        cmix.inputs["Color2"].default_value = (0.80, 0.71, 0.50, 1.0)
        links.new(mask, cmix.inputs["Factor"])
        links.new(color_out, cmix.inputs["Color1"])
        color_out = cmix.outputs["Color"]
        # roughness: matte card vs glossier tape
        rmix = nodes.new("ShaderNodeMixRGB")
        rmix.inputs["Color1"].default_value = (rough, rough, rough, 1.0)
        rmix.inputs["Color2"].default_value = (0.32, 0.32, 0.32, 1.0)
        links.new(mask, rmix.inputs["Factor"])
        links.new(rmix.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        scene_kit._set_input(bsdf, "Roughness", rough)

    links.new(color_out, bsdf.inputs["Base Color"])

    # fine fibre bump so faces aren't dead-flat
    fib = nodes.new("ShaderNodeTexNoise")
    fib.inputs["Scale"].default_value = 220.0
    links.new(coord.outputs["Object"], fib.inputs["Vector"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    links.new(fib.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def place_boxes(placements, container, jitter=0.02, seed=7):
    """Instantiate packed product boxes; centred in world for nicer framing."""
    L, W, H = container
    rng = random.Random(seed)
    coll = bpy.data.collections.new("Cargo")
    bpy.context.scene.collection.children.link(coll)
    objs = []
    for idx, p in enumerate(placements):
        # per-box shade jitter so cartons don't look cloned
        cr, cg, cb = p["color"]
        j = rng.uniform(-jitter, jitter)
        col = (max(0, min(1, cr + j)), max(0, min(1, cg + j)), max(0, min(1, cb + j)))
        mat = cardboard_material(f"Carton_{idx}", base_color=col,
                                 rough=rng.uniform(0.8, 0.9))
        o = _box(f"Box_{idx}_{p['label']}",
                 p["x"], p["x"] + p["l"],
                 p["y"], p["y"] + p["w"],
                 p["z"], p["z"] + p["h"],
                 material=mat, bevel=0.006)
        for c in o.users_collection:
            c.objects.unlink(o)
        coll.objects.link(o)
        objs.append(o)
    return objs


# --------------------------------------------------------------------------- #
#  Camera / lighting for the cargo shot
# --------------------------------------------------------------------------- #
def setup_truck_shot(container, quality="balanced", resolution=(1600, 1200)):
    L, W, H = container
    scene_kit.setup_render(quality=quality, resolution=resolution,
                           exposure=-0.5, look="AgX - Medium High Contrast")
    # warehouse HDRI = realistic light + reflections (falls back to sky if missing)
    if os.path.exists(HDRI_PATH):
        scene_kit.set_world_hdri(HDRI_PATH, strength=1.0, rotation_deg=HDRI_ROT)
    else:
        scene_kit.set_world_sky(sun_elevation_deg=30, sun_rotation_deg=300, strength=0.4)
    # gentle warm key from outside the open back for shadow direction
    scene_kit.add_area_light("DoorLight", (L + 2.6, W * 0.35, H * 0.85),
                             energy=1000, size=4.0, color=(1.0, 0.95, 0.88),
                             target=(L * 0.45, W * 0.55, H * 0.45))
    # camera framed for the full truck (see set_view)
    set_view(container, "hero")


def _cyl(name, radius, depth, location, material=None, segments=28):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth,
                                        location=location, vertices=segments)
    o = bpy.context.active_object
    o.name = name
    o.rotation_euler = (math.radians(90), 0, 0)   # axis along Y (wheel)
    for p in o.data.polygons:
        p.use_smooth = True
    if material:
        scene_kit.assign_material(o, material)
    return o


def _wheel(name, x, y, z, r, depth, tire, hub):
    t = _cyl(name + "_tire", r, depth, (x, y, z), material=tire)
    h = _cyl(name + "_hub", r * 0.42, depth * 1.06, (x, y, z), material=hub)
    return [t, h]


def build_truck_exterior(container, cab_len=2.0, clearance=1.0, wheel_r=0.46,
                         body_color=(0.86, 0.86, 0.88), body_style="box"):
    """Cab + chassis + wheels + open rear roll-door, so the cargo box reads as a truck.
    body_style 'box' = box truck with cab; 'trailer'/'container' = no cab + landing gear."""
    L, W, H = container
    body = scene_kit.make_material("CabBody", base_color=body_color, roughness=0.3, metallic=0.1)
    tire = scene_kit.make_material("Tire", base_color=(0.015, 0.015, 0.018), roughness=0.85)
    hub = scene_kit.make_material("Hub", base_color=(0.55, 0.56, 0.58), roughness=0.25, metallic=0.95)
    glass = scene_kit.make_material("Glass", base_color=(0.02, 0.03, 0.05), roughness=0.05, metallic=0.0)
    chassis = scene_kit.make_material("Chassis", base_color=(0.04, 0.04, 0.04), roughness=0.55, metallic=0.5)

    coll = bpy.data.collections.new("TruckBody")
    bpy.context.scene.collection.children.link(coll)

    def put(o):
        for c in o.users_collection:
            c.objects.unlink(o)
        coll.objects.link(o)

    wheel_cz = -(clearance - wheel_r)            # tire bottom sits at z = -clearance
    has_cab = (body_style == "box")
    rail_x0 = -cab_len if has_cab else -0.1
    light = scene_kit.make_material("HeadLight", base_color=(0.9, 0.9, 0.85),
                                    roughness=0.1, emission_color=(1, 1, 0.9),
                                    emission_strength=1.5)

    if has_cab:
        put(_box("Cab", -cab_len, -0.05, 0.0, W, 0.10, 1.62, material=body, bevel=0.05))
        put(_box("Windshield", -cab_len - 0.005, -cab_len + 0.10, 0.06, W - 0.06, 0.85, 1.5, material=glass))
        put(_box("WinL", -cab_len + 0.10, -0.4, -0.005, 0.06, 0.85, 1.45, material=glass))
        put(_box("WinR", -cab_len + 0.10, -0.4, W - 0.06, W + 0.005, 0.85, 1.45, material=glass))
        put(_box("FrontBumper", -cab_len - 0.14, -cab_len, -0.05, W, 0.0, 0.42, material=chassis))
        put(_box("HL_L", -cab_len - 0.05, -cab_len + 0.02, 0.12, 0.42, 0.45, 0.7, material=light))
        put(_box("HL_R", -cab_len - 0.05, -cab_len + 0.02, W - 0.42, W - 0.12, 0.45, 0.7, material=light))
    else:
        # semi-trailer / container: landing-gear legs near the front underside
        put(_box("LegL", 0.35, 0.52, 0.18, 0.34, -clearance + 0.02, -0.16, material=chassis))
        put(_box("LegR", 0.35, 0.52, W - 0.34, W - 0.18, -clearance + 0.02, -0.16, material=chassis))
        put(_box("LegFoot", 0.28, 0.58, 0.14, W - 0.14, -clearance + 0.02, -clearance + 0.10, material=chassis))

    # chassis rails + rear bumper
    put(_box("RailL", rail_x0, L + 0.15, 0.10, 0.24, -0.18, 0.0, material=chassis))
    put(_box("RailR", rail_x0, L + 0.15, W - 0.24, W - 0.10, -0.18, 0.0, material=chassis))
    put(_box("RearBumper", L + 0.06, L + 0.20, 0.15, W - 0.15, -0.05, 0.32, material=chassis))

    # wheels: rear dual always; front pair only with a cab
    rx = L - 1.05
    wheels = [("RLa", rx, -0.16), ("RLb", rx, 0.12),
              ("RRa", rx, W + 0.16), ("RRb", rx, W - 0.12)]
    if has_cab:
        fx = -cab_len * 0.5
        wheels += [("FL", fx, -0.06), ("FR", fx, W + 0.06)]
    for nm, x, y in wheels:
        for o in _wheel(nm, x, y, wheel_cz, wheel_r, 0.22, tire, hub):
            put(o)
    # rolled-up rear door above the opening
    put(_cyl("RolledDoor", 0.22, W - 0.08, (L - 0.02, W * 0.5, H - 0.16), material=chassis))
    # real concrete floor (sharper + better grounding than the blurry HDRI floor)
    concrete = scene_kit.make_material("WarehouseFloor", base_color=(0.32, 0.31, 0.30),
                                       roughness=0.42, metallic=0.0)
    bpy.ops.mesh.primitive_plane_add(size=120, location=(L * 0.3, W * 0.5, -clearance))
    g = bpy.context.active_object
    g.name = "WarehouseFloor"
    scene_kit.assign_material(g, concrete)
    put(g)
    return coll


def set_view(container, name="hero"):
    """Reposition the camera to a named viewpoint. 'top' hides the ceiling."""
    L, W, H = container
    for o in list(bpy.data.objects):
        if o.type == 'CAMERA':
            bpy.data.objects.remove(o, do_unlink=True)
    ceiling = bpy.data.objects.get("Ceiling")
    if ceiling:
        ceiling.hide_render = (name == "top")
    if name == "rear":
        cam = scene_kit.add_camera((L + 4.0, W * 0.5, 0.75),
                                   (0.2, W * 0.5, H * 0.5), lens=35)
    elif name == "top":
        cam = scene_kit.add_camera((L * 0.5, W * 0.5, H + 6.0),
                                   (L * 0.5, W * 0.5, 0.0), lens=45)
        cam.data.type = 'ORTHO'
        cam.data.ortho_scale = max(L, W) * 1.12
    else:  # hero: 3/4 from rear-right showing wheels, body + load through open door
        cam = scene_kit.add_camera((L + 4.3, W + 3.4, 1.75),
                                   (L * 0.36, W * 0.5, 0.75), lens=40)
    return cam


def build(container=DEFAULT_TRUCK, catalog=None, quality="balanced",
          resolution=(1600, 1200), truck_body=True, truck_style="box", truck_key=None):
    """Full build: clean scene, pack, construct truck + cargo, set up shot. Returns stats."""
    catalog = catalog or DEFAULT_CATALOG
    scene_kit.reset_scene()
    placements, leftovers, stats = pack_best(container, catalog)
    stats["overlaps"] = len(verify_no_overlap(placements))
    if truck_key:
        wmap = {it["label"]: truckspec.unit_weight(it) for it in catalog}
        items = [(p["x"] + p["l"] / 2.0, wmap.get(p["label"], 0.0)) for p in placements]
        axle = truckspec.axle_loads(items, truck_key, container[0])
        if axle:
            stats["axle"] = axle
    build_truck_shell(container)
    place_boxes(placements, container)
    if truck_body:
        build_truck_exterior(container, body_style=truck_style)
    setup_truck_shot(container, quality=quality, resolution=resolution)
    return stats


def loading_order(placements, z_tol=0.035, margin=0.06):
    """Human/optimizer loading sequence (MOJRO/ORTEC-style wall building).

    Support-aware topological order: a box is only loadable once everything it
    rests on is already placed (never floats). Among loadable boxes, prefer the
    FRONT of the truck (low x), then bottom (low z), then across (y) -> builds a
    full wall against the headboard, then steps back toward the door.
    """
    import heapq
    n = len(placements)
    deps = [0] * n
    dependents = [[] for _ in range(n)]
    for i, a in enumerate(placements):
        if a["z"] <= z_tol:                       # rests on the floor
            continue
        for j, b in enumerate(placements):
            if i == j:
                continue
            if abs(a["z"] - (b["z"] + b["h"])) <= z_tol and \
               a["x"] < b["x"] + b["l"] + margin and a["x"] + a["l"] > b["x"] - margin and \
               a["y"] < b["y"] + b["w"] + margin and a["y"] + a["w"] > b["y"] - margin:
                dependents[j].append(i)
                deps[i] += 1

    def key(idx):
        p = placements[idx]
        return (round(p["x"], 3), round(p["z"], 3), round(p["y"], 3), idx)

    heap = [key(i) for i in range(n) if deps[i] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        idx = heapq.heappop(heap)[-1]
        order.append(idx)
        for d in dependents[idx]:
            deps[d] -= 1
            if deps[d] == 0:
                heapq.heappush(heap, key(d))
    if len(order) < n:                            # safety for any cycle
        seen = set(order)
        order += [i for i in range(n) if i not in seen]
    return order


def animate_loading(objs, placements, fps=24, stagger=2, slide_frames=7,
                    slide_dist=0.55, hold=22):
    """Keyframe cartons sliding into place in real loading order: a full wall at
    the front first, then back toward the door. Each box enters from the rear
    (door side) and slides forward into position - no floating, no ceiling clip."""
    scene = bpy.context.scene
    scene.render.fps = fps
    order = loading_order(placements)
    last = 1
    for rank, i in enumerate(order):
        ob = objs[i]
        start = 1 + rank * stagger
        end = start + slide_frames
        base_x = ob.location.x
        ob.hide_render = ob.hide_viewport = True
        ob.keyframe_insert("hide_render", frame=max(1, start - 1))
        ob.keyframe_insert("hide_viewport", frame=max(1, start - 1))
        ob.hide_render = ob.hide_viewport = False
        ob.keyframe_insert("hide_render", frame=start)
        ob.keyframe_insert("hide_viewport", frame=start)
        ob.location.x = base_x + slide_dist        # enters from the open rear (door)
        ob.keyframe_insert("location", index=0, frame=start)
        ob.location.x = base_x                      # slides forward into place
        ob.keyframe_insert("location", index=0, frame=end)
        last = max(last, end)
    scene.frame_start = 1
    scene.frame_end = last + hold
    return scene.frame_end


def build_animation(container=DEFAULT_TRUCK, catalog=None, resolution=(1280, 720),
                    eevee_samples=16, fps=24, stagger=2):
    """Build the scene and keyframe a loading sequence; renders on EEVEE (fast)."""
    catalog = catalog or DEFAULT_CATALOG
    scene_kit.reset_scene()
    placements, leftovers, stats = pack_best(container, catalog)
    stats["overlaps"] = len(verify_no_overlap(placements))
    build_truck_shell(container)
    objs = place_boxes(placements, container)
    setup_truck_shot(container, quality="fast", resolution=resolution)
    L, W, H = container

    # keep the truck enclosed; light the interior so the loader's POV reads well
    cm = bpy.data.materials.get("TruckCeiling")
    if cm and cm.node_tree:
        scene_kit._set_input(cm.node_tree.nodes.get("Principled BSDF"),
                             "Emission Strength", 0.6)

    # loader's point of view: standing at the open rear door, looking in
    for o in list(bpy.data.objects):
        if o.type == 'CAMERA':
            bpy.data.objects.remove(o, do_unlink=True)
    scene_kit.add_camera((L + 2.6, W * 0.5, 1.30),
                         (0.5, W * 0.5, 1.0), lens=26)

    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    try:
        scene.eevee.taa_render_samples = eevee_samples
    except Exception:
        pass
    stats["frames"] = animate_loading(objs, placements, fps=fps, stagger=stagger)
    return stats
