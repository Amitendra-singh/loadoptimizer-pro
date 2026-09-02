# LoadOptimizer Pro

**Play the game:** [Load Bay](https://amitendra-singh.github.io/loadoptimizer-pro/) — cube-out or weight-out, in the browser. Open `index.html`. No build step.

A photorealistic 3D **truck load-optimization** tool. Describe a product portfolio,
and it packs it into a vehicle with a 3D bin-packing engine, tells you whether you'll
**cube-out or weight-out**, **recommends the best truck**, and renders the result —
including a **loader's-eye animation** of the exact order to load each box.

Built end-to-end on free, local tooling: **Blender 5.x** (Cycles path-tracer + EEVEE,
Apple Metal GPU), CC0 assets from Poly Haven, `ffmpeg`, and the Python standard library.
No paid services, no cloud render farm.

```
You (plain English / a product list)
        │
        ▼
  Web UI (webapp.py)  ──►  freight analysis (truckspec.py)  ──►  cube/weight-out + truck recommendation
        │
        ▼
  render_load.py  ──►  pack (truckpack.pack_best)  ──►  build truck + cargo (truckpack + scene_kit)
        │                                                        │
        │                                          Cycles/Metal (photoreal stills)
        │                                          EEVEE (loading animation) ──► ffmpeg mp4
        ▼
  photoreal renders + utilization/payload report
```

## Play Load Bay

Open `index.html` in a browser. Click a carton, click the bay, `R` to yaw, Backspace to undo. **Pack for me** runs a greedy skyline fill — the same idea as the optimiser, not a formula.

Three bays: a van (cube-out), a 16 ft box (weight-out), a 26 ft box (axle). Space and payload race. One of them wins.

## Features

- **3D bin-packing** — a height-map (skyline) packer with stacking + 90° rotation;
  `pack_best` tries multiple loading strategies and keeps the densest (~70–85%, realistic).
- **Cube-out vs weight-out** — every vehicle has a real **volume capacity and payload (kg)**;
  the binding constraint is reported (dense freight weight-outs, bulky freight cubes-out).
- **Truck recommendation** — evaluates the whole fleet and recommends the vehicle that
  carries the portfolio in the fewest trucks at the highest binding-resource utilization.
- **Loader's-eye loading animation** — boxes load in real **wall-building order**
  (front headboard first, then back toward the door, bottom-up, support-aware — nothing
  floats), shown from the perspective of the person at the open rear door.
- **Photoreal renders** — Cycles + a CC0 warehouse HDRI; a procedural box-truck / container
  / trailer body; kraft cartons with packing-tape; hero, top-plan and rear views.
- **Enterprise web UI** — vehicle picker, per-SKU table with weights, dual volume/payload
  fill bars, per-SKU "what fit / what didn't" breakdown, CSV import.

## Prerequisites (macOS, Apple Silicon recommended)

1. **Blender 5.x** at `/Applications/Blender.app` (Cycles uses the Metal GPU automatically).
2. **The BlenderMCP addon** — only needed for *interactive* (live) Blender control via an
   AI client; the app itself renders **headlessly** and does not require it. Addon source:
   <https://github.com/ahujasid/blender-mcp> (install `addon.py`, enable it in Blender).
3. **ffmpeg** — `brew install ffmpeg` (encodes the loading animation; PNG frames otherwise).
4. **Python 3** — only the standard library is used (no `pip install` needed). Blender
   bundles its own Python + numpy for the render/packing side.

> The 4K warehouse HDRI is git-ignored to keep the repo light; the **2K HDRI is included**
> and used as a fallback. To restore 4K, re-download `empty_warehouse_01` (4k) from
> [Poly Haven](https://polyhaven.com/a/empty_warehouse_01) into `assets/empty_warehouse_01_4k.hdr`.

## Run it

**Web UI** (recommended):
```bash
python3 webapp.py          # then open http://localhost:8765
```

**Keep it always running** (macOS LaunchAgent — starts at login, auto-restarts if it dies):
```bash
./service.sh install       # then http://localhost:8765 is always up
./service.sh status        # | restart | logs | uninstall
```
Pick a vehicle, edit the product table (dimensions, quantity, **kg**), and either
**⚖ Recommend best truck** (instant, no render) or **Optimize & Render**.

**Command line** (headless, no GUI):
```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
  --python render_load.py -- products.json ./out
# -> ./out/load_<view>.png + report.json
```
Input can be JSON (see `products.json`) or CSV (`label,l,w,h,count,weight` + optional `r,g,b`).

## Code map (suggested reading order)

| File | Role |
|------|------|
| `GUIDE.md`       | Deeper architecture notes + perf numbers |
| `truckspec.py`   | **Start here** — vehicle catalog (real dims + payloads), recommendation, cube/weight-out. Pure stdlib, no Blender |
| `scene_kit.py`   | Reusable photoreal framework (Cycles/Metal, AgX, denoise, HDRI, PBR, cameras, lights, declarative spec interpreter) |
| `truckpack.py`   | Packing (`pack_skyline`/`pack_best`), `loading_order` (wall-building sequence), truck/cargo/box geometry, `build()` + `build_animation()` |
| `render_load.py` | Headless driver: JSON/CSV → pack → render stills (Cycles) + animation (EEVEE→ffmpeg) → `report.json` |
| `webapp.py`      | Stdlib web server + UI; endpoints `/api/trucks`, `/api/analyze`, `/api/pack` |

## Notes & limitations

- Rendering must run **natively on macOS** (Metal GPU) — don't containerize the render step;
  Docker on Mac can't reach the GPU.
- Packing is a fast heuristic (~74% on mixed cartons). A higher-fill solver
  (OR-Tools / py3dbp) is a natural future upgrade, along with axle-weight balancing and
  LIFO-by-delivery-stop sequencing.
