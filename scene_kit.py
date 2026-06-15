"""
scene_kit.py - Photoreal scene framework for Blender 5.x (Cycles + Apple Metal).

Part of an AI 3D pipeline:
    natural language  ->  Claude/Ollama  ->  scene spec (dict)  ->  this interpreter  ->  Cycles render

Design goals
------------
* Every render starts from physically-correct defaults (Cycles, GPU, AgX, OpenImageDenoise).
* High-level helpers so the "director" (Claude) reasons about art, not API boilerplate.
* A declarative `build_from_spec()` so cheaper/local models can drive it too.

Usage inside Blender's Python (via the MCP execute_blender_code tool):
    import sys; sys.path.insert(0, "/Users/amitendrasinghthenua/blender-mcp/photoreal")
    import importlib, scene_kit; importlib.reload(scene_kit)
    scene_kit.setup_render(quality="balanced", resolution=(1600,1600))
"""

import bpy
import bmesh
import math
import os
from mathutils import Vector

# --------------------------------------------------------------------------- #
#  Quality presets  (samples are post-adaptive ceilings; denoise does the rest)
# --------------------------------------------------------------------------- #
SAMPLE_PRESETS = {
    "draft":    48,
    "fast":     96,
    "balanced": 256,
    "high":     512,
    "ultra":    1024,
}


# --------------------------------------------------------------------------- #
#  Internals
# --------------------------------------------------------------------------- #
def _set_input(node, name, value):
    """Set a shader-node input by name, ignoring renamed/absent sockets."""
    try:
        sock = node.inputs[name]
    except Exception:
        return False
    try:
        if isinstance(value, (tuple, list)) and len(value) == 3 and len(sock.default_value) == 4:
            value = (value[0], value[1], value[2], 1.0)
        sock.default_value = value
        return True
    except Exception:
        return False


def _track_to(obj, target):
    """Point an object's -Z axis at `target` (used for cameras and lights)."""
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()


def ensure_metal_gpu():
    """Enable the Apple Metal GPU for Cycles and route rendering to it."""
    cprefs = bpy.context.preferences.addons['cycles'].preferences
    try:
        cprefs.compute_device_type = 'METAL'
    except Exception:
        pass
    try:
        cprefs.refresh_devices()
    except Exception:
        pass
    enabled = []
    for d in cprefs.devices:
        if d.type == 'METAL':
            d.use = True
            enabled.append(d.name)
        elif d.type == 'CPU':
            # GPU-only is faster + avoids unified-memory duplication on Apple Silicon
            d.use = False
    bpy.context.scene.cycles.device = 'GPU'
    return enabled


# --------------------------------------------------------------------------- #
#  Render configuration
# --------------------------------------------------------------------------- #
def setup_render(quality="balanced", resolution=(1600, 1600), exposure=0.0,
                 look="None", transparent_film=False):
    """Configure Cycles for photoreal output. Returns the chosen sample count."""
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    ensure_metal_gpu()

    c = scene.cycles
    c.samples = SAMPLE_PRESETS.get(quality, 256)
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.01
    c.use_denoising = True
    for attr, val in (("denoiser", 'OPENIMAGEDENOISE'),
                      ("denoising_input_passes", 'RGB_ALBEDO_NORMAL'),
                      ("denoising_prefilter", 'ACCURATE')):
        try:
            setattr(c, attr, val)
        except Exception:
            pass

    # Light paths - generous enough for glass/liquids, capped for speed
    c.max_bounces = 12
    c.diffuse_bounces = 4
    c.glossy_bounces = 6
    c.transmission_bounces = 12
    c.transparent_max_bounces = 8
    c.volume_bounces = 2
    c.caustics_reflective = False
    c.caustics_refractive = False
    scene.render.use_persistent_data = True  # faster re-renders / animation

    # Film + output
    scene.render.film_transparent = transparent_film
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    img = scene.render.image_settings
    img.file_format = 'PNG'
    img.color_depth = '16'

    # Color management - AgX is the modern filmic transform
    vs = scene.view_settings
    try:
        vs.view_transform = 'AgX'
    except Exception:
        pass
    try:
        vs.look = look
    except Exception:
        pass
    vs.exposure = exposure
    return c.samples


# --------------------------------------------------------------------------- #
#  Scene management
# --------------------------------------------------------------------------- #
def reset_scene():
    """Remove all objects and orphaned data for a clean build."""
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


# --------------------------------------------------------------------------- #
#  World / lighting environment
# --------------------------------------------------------------------------- #
def _fresh_world():
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    world.node_tree.nodes.clear()
    return world


def set_world_color(color=(0.05, 0.05, 0.05), strength=1.0):
    world = _fresh_world()
    nt = world.node_tree
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    _set_input(bg, 'Color', (*color, 1.0))
    _set_input(bg, 'Strength', strength)
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    return world


def set_world_hdri(filepath, strength=1.0, rotation_deg=0.0):
    """Image-based lighting from an .hdr/.exr (the realism workhorse)."""
    world = _fresh_world()
    nt = world.node_tree
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    _set_input(bg, 'Strength', strength)
    env = nt.nodes.new('ShaderNodeTexEnvironment')
    env.image = bpy.data.images.load(filepath, check_existing=True)
    mapping = nt.nodes.new('ShaderNodeMapping')
    mapping.inputs['Rotation'].default_value[2] = math.radians(rotation_deg)
    texco = nt.nodes.new('ShaderNodeTexCoord')
    nt.links.new(texco.outputs['Generated'], mapping.inputs['Vector'])
    nt.links.new(mapping.outputs['Vector'], env.inputs['Vector'])
    nt.links.new(env.outputs['Color'], bg.inputs['Color'])
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    return world


def set_world_sky(sun_elevation_deg=18.0, sun_rotation_deg=-30.0, strength=1.0):
    """Procedural Nishita physical sky - great free outdoor lighting."""
    world = _fresh_world()
    nt = world.node_tree
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    _set_input(bg, 'Strength', strength)
    sky = nt.nodes.new('ShaderNodeTexSky')
    try:
        sky.sky_type = 'NISHITA'
        sky.sun_elevation = math.radians(sun_elevation_deg)
        sky.sun_rotation = math.radians(sun_rotation_deg)
    except Exception:
        pass
    nt.links.new(sky.outputs['Color'], bg.inputs['Color'])
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    return world


# --------------------------------------------------------------------------- #
#  Materials
# --------------------------------------------------------------------------- #
def make_material(name, base_color=(0.8, 0.8, 0.8), metallic=0.0, roughness=0.5,
                  ior=1.45, transmission=0.0, coat=0.0,
                  emission_color=None, emission_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        _set_input(bsdf, 'Base Color', base_color)
        _set_input(bsdf, 'Metallic', metallic)
        _set_input(bsdf, 'Roughness', roughness)
        _set_input(bsdf, 'IOR', ior)
        if not _set_input(bsdf, 'Transmission Weight', transmission):
            _set_input(bsdf, 'Transmission', transmission)
        _set_input(bsdf, 'Coat Weight', coat)
        if emission_color:
            _set_input(bsdf, 'Emission Color', emission_color)
            _set_input(bsdf, 'Emission Strength', emission_strength)
    return mat


def assign_material(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return obj


# --------------------------------------------------------------------------- #
#  Geometry helpers
# --------------------------------------------------------------------------- #
def add_primitive(kind="cube", location=(0, 0, 0), scale=(1, 1, 1),
                  rotation_deg=(0, 0, 0), name=None, smooth=True):
    ops = {
        "cube":     bpy.ops.mesh.primitive_cube_add,
        "sphere":   bpy.ops.mesh.primitive_uv_sphere_add,
        "ico":      bpy.ops.mesh.primitive_ico_sphere_add,
        "cylinder": bpy.ops.mesh.primitive_cylinder_add,
        "cone":     bpy.ops.mesh.primitive_cone_add,
        "torus":    bpy.ops.mesh.primitive_torus_add,
        "plane":    bpy.ops.mesh.primitive_plane_add,
        "monkey":   bpy.ops.mesh.primitive_monkey_add,
    }
    ops.get(kind, bpy.ops.mesh.primitive_cube_add)(location=location)
    obj = bpy.context.active_object
    obj.scale = scale
    obj.rotation_euler = [math.radians(a) for a in rotation_deg]
    if name:
        obj.name = name
    if smooth and kind in ("sphere", "ico", "cylinder", "cone", "torus", "monkey"):
        for p in obj.data.polygons:
            p.use_smooth = True
    return obj


def add_ground(size=40, material=None, shadow_catcher=False):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "Ground"
    if shadow_catcher:
        obj.is_shadow_catcher = True
    if material:
        assign_material(obj, material)
    return obj


def add_studio_cyclorama(width=24.0, floor_depth=12.0, wall_height=14.0,
                         fillet_radius=5.0, segments=24, material=None):
    """Seamless curved floor->wall backdrop ('infinity cove') for studio shots."""
    profile = []                      # (y, z) side profile
    profile.append((-floor_depth, 0.0))
    profile.append((0.0, 0.0))
    cx, cz = 0.0, fillet_radius       # fillet arc center
    for i in range(1, segments + 1):
        a = (math.pi / 2) * (i / segments)
        profile.append((cx + fillet_radius * math.sin(a),
                        cz - fillet_radius * math.cos(a)))
    profile.append((fillet_radius, wall_height))

    bm = bmesh.new()
    left, right = [], []
    for (y, z) in profile:
        left.append(bm.verts.new((-width / 2, y, z)))
        right.append(bm.verts.new((width / 2, y, z)))
    bm.verts.ensure_lookup_table()
    for i in range(len(profile) - 1):
        bm.faces.new((left[i], left[i + 1], right[i + 1], right[i]))
    mesh = bpy.data.meshes.new("Cyclorama")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Cyclorama", mesh)
    bpy.context.collection.objects.link(obj)
    for p in obj.data.polygons:
        p.use_smooth = True
    if material:
        assign_material(obj, material)
    return obj


# --------------------------------------------------------------------------- #
#  Lights
# --------------------------------------------------------------------------- #
def add_area_light(name, location, energy=1000.0, size=2.0, color=(1, 1, 1),
                   target=(0, 0, 0), shape='SQUARE'):
    ld = bpy.data.lights.new(name, 'AREA')
    ld.energy = energy
    ld.size = size
    ld.color = color
    ld.shape = shape
    obj = bpy.data.objects.new(name, ld)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    _track_to(obj, target)
    return obj


def three_point(target=(0, 0, 0.6), key=1200.0, fill=350.0, rim=900.0, scale=1.0):
    """Classic studio key/fill/rim rig pointed at `target`."""
    tx, ty, tz = target
    k = add_area_light("Key",  (tx - 3, ty - 4, tz + 3), energy=key,  size=3.0 * scale, target=target)
    f = add_area_light("Fill", (tx + 4, ty - 2, tz + 1.5), energy=fill, size=4.0 * scale, target=target)
    r = add_area_light("Rim",  (tx + 1, ty + 5, tz + 4), energy=rim,  size=2.0 * scale, target=target,
                       color=(1.0, 0.98, 0.95))
    return k, f, r


# --------------------------------------------------------------------------- #
#  Camera
# --------------------------------------------------------------------------- #
def add_camera(location=(0, -4, 1.4), look_at=(0, 0, 0.6), lens=85.0,
               fstop=None, focus_distance=None, name="Camera"):
    cam_data = bpy.data.cameras.new(name)
    cam = bpy.data.objects.new(name, cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = location
    cam_data.lens = lens
    _track_to(cam, look_at)
    if fstop:
        cam_data.dof.use_dof = True
        cam_data.dof.aperture_fstop = fstop
        cam_data.dof.focus_distance = (focus_distance
                                       if focus_distance is not None
                                       else (Vector(look_at) - Vector(location)).length)
    bpy.context.scene.camera = cam
    return cam


# --------------------------------------------------------------------------- #
#  Render
# --------------------------------------------------------------------------- #
def render_to(filepath, samples=None):
    scene = bpy.context.scene
    if samples:
        scene.cycles.samples = samples
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)
    return filepath


def render_sequence(out_dir, prefix="frame_"):
    """Render the active frame range as a PNG sequence (use EEVEE for speed)."""
    scene = bpy.context.scene
    scene.render.image_settings.file_format = 'PNG'
    os.makedirs(out_dir, exist_ok=True)
    scene.render.filepath = os.path.join(out_dir, prefix)
    bpy.ops.render.render(animation=True)
    return out_dir


# --------------------------------------------------------------------------- #
#  Declarative spec interpreter
# --------------------------------------------------------------------------- #
def build_from_spec(spec):
    """
    Build an entire scene from a dict. Schema (all keys optional):
      render:   {quality, resolution:[w,h], exposure, transparent}
      world:    {type:"hdri"|"sky"|"color", path, strength, rotation_deg,
                 sun_elevation_deg, sun_rotation_deg, color}
      ground:   {enabled, size, shadow_catcher, material:<mat-spec>}
      cyclorama:{enabled, material:<mat-spec>}
      materials:{name: {base_color, metallic, roughness, ior, transmission,
                        coat, emission_color, emission_strength}}
      objects:  [{kind, name, location, scale, rotation_deg, material:<name>}]
      lights:   [{type:"area", name, location, energy, size, color, target}]
                or {rig:"three_point", target, key, fill, rim}
      camera:   {location, look_at, lens, fstop, focus_distance}
    """
    reset_scene()
    r = spec.get("render", {})
    setup_render(quality=r.get("quality", "balanced"),
                 resolution=tuple(r.get("resolution", (1600, 1600))),
                 exposure=r.get("exposure", 0.0),
                 transparent_film=r.get("transparent", False))

    w = spec.get("world", {})
    wt = w.get("type", "color")
    if wt == "hdri" and w.get("path"):
        set_world_hdri(w["path"], w.get("strength", 1.0), w.get("rotation_deg", 0.0))
    elif wt == "sky":
        set_world_sky(w.get("sun_elevation_deg", 18.0),
                      w.get("sun_rotation_deg", -30.0), w.get("strength", 1.0))
    else:
        set_world_color(tuple(w.get("color", (0.05, 0.05, 0.05))), w.get("strength", 1.0))

    mats = {}
    for mname, mspec in spec.get("materials", {}).items():
        mats[mname] = make_material(mname, **mspec)

    def _resolve_mat(ref):
        if isinstance(ref, dict):
            return make_material(ref.get("name", "mat"), **{k: v for k, v in ref.items() if k != "name"})
        return mats.get(ref)

    g = spec.get("ground", {})
    if g.get("enabled"):
        add_ground(size=g.get("size", 40),
                   material=_resolve_mat(g.get("material")),
                   shadow_catcher=g.get("shadow_catcher", False))

    cyc = spec.get("cyclorama", {})
    if cyc.get("enabled"):
        add_studio_cyclorama(material=_resolve_mat(cyc.get("material")))

    for o in spec.get("objects", []):
        obj = add_primitive(kind=o.get("kind", "cube"),
                            location=tuple(o.get("location", (0, 0, 0))),
                            scale=tuple(o.get("scale", (1, 1, 1))),
                            rotation_deg=tuple(o.get("rotation_deg", (0, 0, 0))),
                            name=o.get("name"))
        if o.get("material"):
            m = _resolve_mat(o["material"])
            if m:
                assign_material(obj, m)

    lights = spec.get("lights", [])
    if isinstance(lights, dict) and lights.get("rig") == "three_point":
        three_point(target=tuple(lights.get("target", (0, 0, 0.6))),
                    key=lights.get("key", 1200.0),
                    fill=lights.get("fill", 350.0),
                    rim=lights.get("rim", 900.0))
    else:
        for L in lights:
            add_area_light(L.get("name", "Light"),
                           tuple(L.get("location", (0, 0, 4))),
                           energy=L.get("energy", 1000.0),
                           size=L.get("size", 2.0),
                           color=tuple(L.get("color", (1, 1, 1))),
                           target=tuple(L.get("target", (0, 0, 0))))

    cam = spec.get("camera", {})
    add_camera(location=tuple(cam.get("location", (0, -4, 1.4))),
               look_at=tuple(cam.get("look_at", (0, 0, 0.6))),
               lens=cam.get("lens", 85.0),
               fstop=cam.get("fstop"),
               focus_distance=cam.get("focus_distance"))
    return spec
