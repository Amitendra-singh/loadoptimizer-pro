# Photoreal Truck-Loading Pipeline — Guide

A zero-extra-cost system for generating near-photorealistic 3D scenes in Blender,
driven by AI. Built for **load-optimization visualization** (best way to fit
products into a truck) but the rendering core is general-purpose.

## How it works (the architecture)

```
You (plain English)  ─►  Claude (director, via Blender MCP)  ─►  Blender 5.x
                                       │
        ┌──────────────────────────────┼───────────────────────────┐
        ▼                              ▼                            ▼
  Poly Haven (free CC0          scene_kit.py                 Cycles + Metal GPU
  HDRIs / textures / models)    (render presets, camera,     (path-traced realism
        │                        lighting, materials,         on Apple M4)
        │                        spec interpreter)            + OpenImageDenoise
        ▼                              ▼                            ▼
  realistic light & surfaces    truckpack.py                 AgX filmic color
                                (3D bin-packer +        ─►   Photoreal PNG
                                 cargo builder)
```

**Why it's free:** Cycles, EEVEE, AgX, OpenImageDenoise, and the Metal GPU backend
are all built into Blender. Poly Haven assets are CC0 (no cost, no attribution).
Claude (your existing subscription) is the only "intelligence" needed; your local
Ollama models (qwen/gemma) can optionally draft scene/product ideas offline.

## Files

| File | Role |
|------|------|
| `scene_kit.py`  | General photoreal framework: `setup_render`, `set_world_hdri/sky/color`, `make_material`, `add_camera`, `add_area_light`, `three_point`, `add_studio_cyclorama`, `build_from_spec`, … |
| `truckpack.py`  | The truck app: `pack()` (3D bin-packing), `build()` (pack + construct cargo + light + camera), `cardboard_material`, etc. |
| `GUIDE.md`      | This file. |
| `../renders/`   | Output images. |

## Using it (three ways)

**1. Just ask Claude** (easiest — you're level 0, this is the intended path):
> "Load a 5×2.3×2.2 m truck with 30 appliance boxes and 80 small boxes, show me the fill %."
> "Make the cartons darker and add a forklift."
> "Render a top-down view at 4K."

Claude reasons about the art + writes/curates the Blender Python live over MCP.

**2. Call the builder with your own product catalog:**
```python
import truckpack, scene_kit
catalog = [
    dict(label="tv",    l=1.2, w=0.15, h=0.75, color=(0.34,0.19,0.09), count=18),
    dict(label="small", l=0.4, w=0.3,  h=0.3,  color=(0.40,0.25,0.13), count=120),
]
stats = truckpack.build(container=(5.0, 2.30, 2.20), catalog=catalog, quality="high")
# stats -> {placed, leftover, utilization %, overlaps:0, ...}
scene_kit.render_to("/path/out.png", samples=384)
```

**3. Declarative scene spec** (lets cheaper/local models drive it):
```python
scene_kit.build_from_spec({
  "render": {"quality": "high", "resolution": [2000,1500]},
  "world":  {"type": "hdri", "path": "/path/to.hdr", "strength": 1.0},
  "objects":[{"kind":"sphere","location":[0,0,0.6],"material":"glass"}],
  "materials":{"glass":{"transmission":1.0,"roughness":0.0,"ior":1.45}},
  "camera": {"location":[0,-4,1.4],"look_at":[0,0,0.6],"lens":85,"fstop":2.8},
})
```

## Run it as an app (no Blender GUI needed)

**Command line** — render from a product list (JSON or CSV) headlessly on the GPU:
```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
  --python ~/blender-mcp/photoreal/render_load.py -- products.json ~/blender-mcp/renders/app
# -> writes load_<view>.png + report.json  (stats: placed, leftover, utilization %, overlaps)
```
CSV form: header `label,l,w,h,count,r,g,b` (r,g,b optional, 0-1).

**Web UI** — a browser tool to type/paste a load and see the render + fit stats:
```bash
python3 ~/blender-mcp/photoreal/webapp.py     # then open http://localhost:8765
```
Zero dependencies (Python stdlib). Edit the product table, pick views (hero / top / rear),
hit **Pack & Render** — the page calls Blender headless and shows the photoreal result
plus space-used %. Note: the render must run natively on macOS (Metal GPU); don't put the
render step in Docker — Docker on Mac can't reach the GPU. The web server itself could be
containerised, calling the host Blender, if you ever want that.

**🎬 Loading-sequence animation** — tick "Animate how to load it" in the UI (or set
`"animate": true` in the spec). It keyframes every carton dropping into place in human
**loading order** (front→back, bottom→top), renders on **EEVEE** (fast: ~0.2s/frame
headless), and `ffmpeg` encodes a `load_anim.mp4`. This is the loader's guide: it shows
the exact order and position to stack each box. Tunables: `anim_resolution`, `fps`,
`stagger` (frames between boxes — lower = faster video). Stills use Cycles; the animation
uses EEVEE — best of both.

## Plugging in a real packing algorithm

`truckpack.pack()` is a deterministic 3D layered-shelf heuristic (no overlaps,
axis-aligned, ~70% fill on mixed loads). To use a stronger optimizer (e.g.
`py3dbp`, OR-Tools, or your own), produce a list of placements:
```python
placements = [dict(x=, y=, z=, l=, w=, h=, color=(r,g,b), label=""), ...]  # min-corner coords
truckpack.place_boxes(placements, container)   # visualize any optimizer's output
```

## Quality presets & performance (measured on this M4 Pro, 20-core GPU)

| Preset    | Samples | 1600×1200 | 2000×1500 |
|-----------|--------:|----------:|----------:|
| `fast`    | 96      | ~5 s      | —         |
| `balanced`| 256     | ~10 s     | ~15 s     |
| `high`    | 512     | —         | ~19–25 s  |

OpenImageDenoise means even low sample counts come out clean.

## Reconnecting Blender (if the MCP says "could not connect")
Just `open -a Blender` and wait ~2 s — the BlenderMCP addon auto-starts the
socket server on port 9876. Then Claude's `mcp__blender__*` tools work again.

## Realism levers (highest impact first)
1. **HDRI lighting** — the #1 factor. `empty_warehouse_01` is loaded; swap any Poly Haven HDRI.
2. **Real PBR textures** on surfaces (kraft cardboard, plywood, steel) — Poly Haven `textures`.
3. **Exposure / AgX look** — keep lit surfaces in midtones so colors stay rich.
4. **Camera** — 30–35 mm for interiors, a slight 3/4 angle for depth, mild f-stop for DoF.
5. **A real truck body / cutaway** for an unmistakable "loaded truck" hero.
