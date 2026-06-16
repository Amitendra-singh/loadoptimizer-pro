"""
packer.py - Pure-Python 3D bin-packing + load stats (numpy only, NO Blender).

The web server imports this to pack any vehicle and drill into fit/no-fit per SKU
INSTANTLY (no Blender boot, no render). The packing functions mirror those in
truckpack.py byte-for-byte so a drill-down matches what a full render would pack;
keep the two in sync if the packing logic changes.
"""

import os
import sys

KIT = os.path.dirname(os.path.abspath(__file__))
if KIT not in sys.path:
    sys.path.insert(0, KIT)
import truckspec  # noqa: E402

DEFAULT_TRUCK = (5.0, 2.30, 2.20)

DEFAULT_CATALOG = [
    dict(label="appliance", l=0.62, w=0.60, h=0.82, color=(0.34, 0.19, 0.09), count=20),
    dict(label="medium",    l=0.52, w=0.40, h=0.40, color=(0.50, 0.33, 0.17), count=50),
    dict(label="small",     l=0.40, w=0.30, h=0.30, color=(0.40, 0.25, 0.13), count=70),
    dict(label="long",      l=1.00, w=0.32, h=0.30, color=(0.30, 0.18, 0.09), count=24),
    dict(label="retail",    l=0.44, w=0.40, h=0.50, color=(0.72, 0.70, 0.66), count=34),
    dict(label="blue",      l=0.50, w=0.50, h=0.34, color=(0.10, 0.24, 0.46), count=28),
]

_PACK_STRATEGIES = {
    "volume":    lambda b: (b["l"] * b["w"] * b["h"], b["h"]),
    "height":    lambda b: (b["h"], b["l"] * b["w"]),
    "footprint": lambda b: (b["l"] * b["w"], b["h"]),
    "longest":   lambda b: (max(b["l"], b["w"], b["h"]), b["l"] * b["w"]),
}


def pack_best(container, catalog, strategies=("volume", "height", "footprint", "longest"),
              balance=False, target_x=None):
    """Try several loading strategies and keep the densest result."""
    best = None
    for s in strategies:
        pl, lo, st = pack_skyline(container, catalog, strategy=s,
                                  balance=balance, target_x=target_x)
        if best is None or st["used_vol"] > best[2]["used_vol"]:
            best = (pl, lo, st)
    return best


def pack_skyline(container, catalog, cell=0.04, flat_tol=0.02, allow_rotate=True,
                 strategy="volume", balance=False, target_x=None):
    """3D height-map (skyline) packer: bottom-left-fill with stacking + yaw rotation."""
    import numpy as np
    L, W, H = container
    nx = max(1, int(round(L / cell)))
    ny = max(1, int(round(W / cell)))
    cx, cy = L / nx, W / ny

    items = []
    for it in catalog:
        wt = truckspec.unit_weight(it)
        color = it.get("color", (0.6, 0.45, 0.3))
        for _ in range(it["count"]):
            d = {"l": it["l"], "w": it["w"], "h": it["h"],
                 "label": it.get("label", "sku"), "color": color, "weight": wt}
            items.append(d)
    max_w = max((d["weight"] for d in items), default=1.0) or 1.0
    items.sort(key=_PACK_STRATEGIES.get(strategy, _PACK_STRATEGIES["volume"]), reverse=True)

    grid = np.zeros((nx, ny), dtype=float)
    bias_x = (np.arange(nx)[:, None] * 1e-4)
    bias_y = (np.arange(ny)[None, :] * 1e-5)

    placements, leftovers = [], []
    for b in items:
        best = None
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
            if balance and target_x is not None:
                wn = b["weight"] / max_w
                xc = np.arange(ox) * cx + lx / 2.0
                penx = np.abs(xc - target_x)[:, None]
                bucket = np.round(M / 0.08) * 0.08
                score = np.where(valid, bucket + 0.012 * wn * penx
                                 + 1e-4 * np.arange(ox)[:, None] + bias_y[:, :oy], np.inf)
            else:
                score = np.where(valid, M + bias_x[:ox] + bias_y[:, :oy], np.inf)
            idx = int(np.argmin(score))
            i, j = divmod(idx, oy)
            sup = float(M[i, j])
            sc = float(score[i, j])
            if best is None or sc < best[0] - 1e-12:
                best = (sc, sup, i, j, lx, wy, fx, fy)
        if best is None:
            leftovers.append(b)
            continue
        _, sup, i, j, lx, wy, fx, fy = best
        placements.append(dict(x=i * cx, y=j * cy, z=sup,
                               l=lx, w=wy, h=b["h"],
                               color=b["color"], label=b["label"], weight=b["weight"]))
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


def balance_target_x(container, truck_key):
    """CG x where front/rear axle utilization equalizes (for balanced packing)."""
    if not (truck_key and truck_key in truckspec.TRUCKS):
        return None
    ax = truckspec.TRUCKS[truck_key].get("axle")
    if not ax:
        return None
    fl, rl = ax["front_limit_kg"], ax["rear_limit_kg"]
    return (rl * ax["rear_x"] + fl * ax["front_x"]) / (fl + rl)


def load_stats(container, catalog, truck_key=None, balance=False):
    """Pack + full stats (breakdown, overlaps, weight, cube/weight-out, axle) with
    NO rendering. Returns (placements, stats). Used both by the Blender build and
    by the web server for instant per-vehicle drill-downs."""
    target_x = balance_target_x(container, truck_key) if balance else None
    placements, leftovers, stats = pack_best(container, catalog,
                                             balance=balance, target_x=target_x)
    stats["overlaps"] = len(verify_no_overlap(placements))
    if truck_key:
        items = [(p["x"] + p["l"] / 2.0, p.get("weight", 0.0)) for p in placements]
        axle = truckspec.axle_loads(items, truck_key, container[0])
        if axle:
            stats["axle"] = axle
        placed_wt = sum(p.get("weight", 0.0) for p in placements)
        lb = truckspec.load_binding(catalog, truck_key,
                                    placed_volume=stats["used_vol"], placed_weight=placed_wt)
        stats.update(weight_kg=lb["weight_kg"], payload_kg=lb["payload_kg"],
                     wt_pct=lb["wt_pct"], binding=lb["binding"], truck_key=truck_key)
    return placements, stats
