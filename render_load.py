"""
render_load.py - Headless app driver for the truck load-optimizer.

Run:
    blender --background --python render_load.py -- <input.json|input.csv> <out_dir>

Input JSON:
    {
      "container":  [5.0, 2.30, 2.20],          # L,W,H metres (optional)
      "quality":    "high",                       # draft|fast|balanced|high|ultra
      "resolution": [1600, 1200],
      "views":      ["hero", "rear", "top"],      # any subset
      "products": [
        {"label":"appliance","l":0.62,"w":0.60,"h":0.82,"count":20,"color":[0.34,0.19,0.09]},
        ...
      ]
    }

Input CSV (header required):  label,l,w,h,count,r,g,b   (r,g,b optional)

Writes <out_dir>/load_<view>.png for each view and <out_dir>/report.json:
    {"stats": {placed, leftover, utilization, overlaps, ...}, "images": {view: path}}
"""

import bpy
import sys
import os
import json
import csv
import shutil
import subprocess

KIT_DIR = os.path.dirname(os.path.abspath(__file__))
if KIT_DIR not in sys.path:
    sys.path.insert(0, KIT_DIR)
import scene_kit          # noqa: E402
import truckpack          # noqa: E402
import truckspec          # noqa: E402

DEFAULT_COLOR = (0.42, 0.27, 0.14)


def _argv_after_ddash():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def load_spec(path):
    if path.lower().endswith(".csv"):
        products = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                col = DEFAULT_COLOR
                if row.get("r") not in (None, ""):
                    col = (float(row["r"]), float(row["g"]), float(row["b"]))
                products.append(dict(label=row.get("label", "box"),
                                     l=float(row["l"]), w=float(row["w"]),
                                     h=float(row["h"]), count=int(float(row["count"])),
                                     color=col))
        return {"products": products}
    with open(path) as f:
        return json.load(f)


def encode_video(frame_dir, prefix, out_mp4, fps):
    ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    pattern = os.path.join(frame_dir, prefix + "%04d.png")
    subprocess.run([ff, "-y", "-framerate", str(fps), "-start_number", "1",
                    "-i", pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "20", "-movflags", "+faststart",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", out_mp4],
                   check=True, capture_output=True, text=True)
    return out_mp4


def main():
    args = _argv_after_ddash()
    if not args:
        print("RENDER_LOAD_ERROR: need <input.json|csv> [out_dir]")
        return
    inp = args[0]
    out_dir = os.path.abspath(args[1] if len(args) > 1
                              else os.path.join(KIT_DIR, "..", "renders"))
    os.makedirs(out_dir, exist_ok=True)

    spec = load_spec(inp)
    container = tuple(spec.get("container", truckpack.DEFAULT_TRUCK))
    quality = spec.get("quality", "balanced")
    resolution = tuple(spec.get("resolution", (1600, 1200)))
    fps = int(spec.get("fps", 24))
    animate = bool(spec.get("animate"))
    truck_key = spec.get("truck_key")
    if truck_key and truck_key in truckspec.TRUCKS:
        truck_style = truckspec.TRUCKS[truck_key]["style"]
    else:
        truck_style = spec.get("truck_style", "box")
        truck_key = truck_key or "box16"
    views = spec.get("views", [])
    if not views and not animate:
        views = ["hero"]

    catalog = spec.get("products") or truckpack.DEFAULT_CATALOG
    for it in catalog:
        it.setdefault("color", DEFAULT_COLOR)
        it["color"] = tuple(it["color"])

    stats, images, video = None, {}, None

    # photoreal stills (Cycles)
    if views:
        stats = truckpack.build(container=container, catalog=catalog,
                                quality=quality, resolution=resolution,
                                truck_style=truck_style, truck_key=truck_key)
        for v in views:
            truckpack.set_view(container, v)
            path = os.path.join(out_dir, f"load_{v}.png")
            scene_kit.render_to(path)
            images[v] = path

    # loading-sequence animation (EEVEE -> PNG frames -> ffmpeg mp4)
    if animate:
        a_res = tuple(spec.get("anim_resolution", (1280, 720)))
        astats = truckpack.build_animation(container=container, catalog=catalog,
                                           resolution=a_res, fps=fps,
                                           stagger=int(spec.get("stagger", 2)))
        frames_dir = os.path.join(out_dir, "frames")
        shutil.rmtree(frames_dir, ignore_errors=True)
        scene_kit.render_sequence(frames_dir, "frame_")
        video = os.path.join(out_dir, "load_anim.mp4")
        encode_video(frames_dir, "frame_", video, fps)
        shutil.rmtree(frames_dir, ignore_errors=True)
        if stats is None:
            stats = astats
        else:
            stats["frames"] = astats.get("frames")

    # weight-out / cube-out for the rendered truck + portfolio recommendation
    if stats:
        wmap = {p["label"]: p for p in catalog}
        placed_wt = sum(truckspec.unit_weight(wmap[b["label"]]) * b["placed"]
                        for b in stats.get("breakdown", []) if b["label"] in wmap)
        lb = truckspec.load_binding(catalog, truck_key,
                                    placed_volume=stats.get("used_vol"),
                                    placed_weight=placed_wt)
        stats.update(weight_kg=lb["weight_kg"], payload_kg=lb["payload_kg"],
                     wt_pct=lb["wt_pct"], binding=lb["binding"], truck_key=truck_key)

    report = {"stats": stats, "images": images,
              "recommendation": truckspec.recommend(catalog)}
    if video and os.path.exists(video):
        report["video"] = video
    rpath = os.path.join(out_dir, "report.json")
    with open(rpath, "w") as f:
        json.dump(report, f, indent=2)
    print("RENDER_LOAD_REPORT " + json.dumps(report))


if __name__ == "__main__":
    main()
