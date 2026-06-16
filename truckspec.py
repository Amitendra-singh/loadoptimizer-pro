"""
truckspec.py - Vehicle catalog + freight analysis (cube-out / weight-out) +
truck recommendation. Pure stdlib (no Blender) so the web UI gets instant answers
and the render pipeline shares one source of truth.

Cube-out  = you run out of SPACE before hitting the weight limit  (light/bulky freight).
Weight-out = you hit the PAYLOAD limit before filling the space    (dense freight).
Real trucks have both a volume capacity and a payload (kg) limit; the binding one wins.
"""

import math

DEFAULT_DENSITY = 250.0   # kg/m3 fallback when a SKU has no weight entered
PACK_EFF = 0.80           # realistic max volumetric fill for mixed cartons

# Interior dims (m) and payload (kg) are real-world-representative figures.
# 'style' selects the 3D body model: box (with cab) | container | trailer.
# 'axle' models the chassis as a 2-support beam in cargo-relative coords
# (x=0 = front of cargo behind the cab, x=L = rear door). front_x can be
# negative (steer axle sits under the cab, ahead of the cargo).
TRUCKS = {
    "van":       {"name": "Cargo Van",        "dims": [3.00, 1.70, 1.80], "payload_kg": 1400,  "style": "box",
                  "axle": {"front_x": -0.6, "rear_x": 2.4, "front_limit_kg": 630, "rear_limit_kg": 1120,
                           "front_name": "Steer axle", "rear_name": "Drive axle"}},
    "box16":     {"name": "Box Truck 16 ft",  "dims": [4.90, 2.40, 2.10], "payload_kg": 3000,  "style": "box",
                  "axle": {"front_x": -1.0, "rear_x": 3.9, "front_limit_kg": 1350, "rear_limit_kg": 2400,
                           "front_name": "Steer axle", "rear_name": "Drive axle"}},
    "box26":     {"name": "Box Truck 26 ft",  "dims": [7.90, 2.60, 2.70], "payload_kg": 5000,  "style": "box",
                  "axle": {"front_x": -1.2, "rear_x": 6.7, "front_limit_kg": 2250, "rear_limit_kg": 4000,
                           "front_name": "Steer axle", "rear_name": "Drive axle"}},
    "cont20":    {"name": "20 ft Container",  "dims": [5.90, 2.35, 2.39], "payload_kg": 21700, "style": "container",
                  "axle": {"front_x": 0.7, "rear_x": 4.8, "front_limit_kg": 14000, "rear_limit_kg": 15420,
                           "front_name": "Kingpin (tractor)", "rear_name": "Trailer tandem"}},
    "cont40":    {"name": "40 ft Container",  "dims": [12.00, 2.35, 2.39], "payload_kg": 26500, "style": "container",
                  "axle": {"front_x": 1.0, "rear_x": 10.2, "front_limit_kg": 14000, "rear_limit_kg": 15420,
                           "front_name": "Kingpin (tractor)", "rear_name": "Trailer tandem"}},
    "trailer53": {"name": "53 ft Trailer",    "dims": [16.10, 2.60, 2.90], "payload_kg": 21772, "style": "trailer",
                  "axle": {"front_x": 1.2, "rear_x": 13.0, "front_limit_kg": 14000, "rear_limit_kg": 15420,
                           "front_name": "Kingpin (tractor)", "rear_name": "Trailer tandem"}},
}
TRUCK_ORDER = ["van", "box16", "box26", "cont20", "cont40", "trailer53"]

# vehicle class: drivable box trucks vs units that need a tractor/chassis.
_CLASS = {"van": "Box truck", "box16": "Box truck", "box26": "Box truck",
          "trailer53": "Semi-trailer", "cont20": "Container", "cont40": "Container"}
_CLASS_RANK = {"Box truck": 0, "Semi-trailer": 1, "Container": 2}


def unit_weight(p):
    w = p.get("weight")
    if not w:
        return p["l"] * p["w"] * p["h"] * DEFAULT_DENSITY
    return float(w)


def portfolio_totals(products):
    vol = sum(p["l"] * p["w"] * p["h"] * p["count"] for p in products)
    wt = sum(unit_weight(p) * p["count"] for p in products)
    units = sum(p["count"] for p in products)
    return vol, wt, units


def truck_volume(key):
    d = TRUCKS[key]["dims"]
    return d[0] * d[1] * d[2]


def _fits(p, t):
    """A SKU fits a truck if it can be oriented within the interior dims."""
    return all(a <= b + 1e-6 for a, b in zip(sorted([p["l"], p["w"], p["h"]]), sorted(t["dims"])))


def analyze_truck(products, key, pack_eff=PACK_EFF):
    t = TRUCKS[key]
    vol, wt, _ = portfolio_totals(products)
    tv = truck_volume(key)
    usable = tv * pack_eff
    feasible = all(_fits(p, t) for p in products) if products else True
    n_vol = math.ceil(vol / usable) if usable > 0 and vol > 0 else 1
    n_wt = math.ceil(wt / t["payload_kg"]) if t["payload_kg"] > 0 and wt > 0 else 1
    n = max(1, n_vol, n_wt)
    vol_pct = round(100 * vol / (n * tv), 1) if tv else 0
    wt_pct = round(100 * wt / (n * t["payload_kg"]), 1) if t["payload_kg"] else 0
    # binding resource: more trucks wins; if equal, whichever is more utilized
    if n_wt > n_vol:
        binding = "weight"
    elif n_vol > n_wt:
        binding = "cube"
    elif abs(wt_pct - vol_pct) <= 8:
        binding = "even"
    else:
        binding = "weight" if wt_pct > vol_pct else "cube"
    return {
        "key": key, "name": t["name"], "style": t["style"], "class": _CLASS[key],
        "feasible": feasible,
        "trucks": n, "n_vol": n_vol, "n_wt": n_wt, "binding": binding,
        "vol_pct": vol_pct, "wt_pct": wt_pct,
        "payload_kg": t["payload_kg"], "volume_m3": round(tv, 1), "dims": t["dims"],
    }


def recommend(products, pack_eff=PACK_EFF):
    """Rank vehicles and return a shortlist. Order: fewest trucks, then fullest on
    the binding resource (in 10% bands so near-ties don't flip), then prefer a
    drivable box truck over a semi/container, then the smallest capacity. This
    avoids over-specifying a container when a box truck fits just as well."""
    vol, wt, units = portfolio_totals(products)
    rows = [analyze_truck(products, k, pack_eff) for k in TRUCK_ORDER]
    pool = [r for r in rows if r["feasible"]] or rows

    def fill_of(r):
        return r["wt_pct"] if r["binding"] == "weight" else r["vol_pct"]

    ranked = sorted(pool, key=lambda r: (r["trucks"], -round(fill_of(r) / 10.0),
                                         _CLASS_RANK[r["class"]], truck_volume(r["key"])))
    shortlist = ranked[:3]
    best = shortlist[0]
    if best["binding"] == "weight":
        reason = (f"Weight-out — payload is the limit ({best['wt_pct']}% of "
                  f"{best['payload_kg']:,} kg used; only {best['vol_pct']}% of the space).")
    elif best["binding"] == "cube":
        reason = (f"Cube-out — space is the limit ({best['vol_pct']}% of volume used; "
                  f"only {best['wt_pct']}% of payload).")
    else:
        reason = f"Balanced — volume and weight fill together ({best['vol_pct']}% / {best['wt_pct']}%)."
    label = {"weight": "weight-out", "cube": "cube-out", "even": "balanced"}[best["binding"]]
    return {
        "recommended": best["key"], "recommended_name": best["name"],
        "trucks": best["trucks"], "binding": label, "reason": reason,
        "shortlist": shortlist,
        "totals": {"volume_m3": round(vol, 2), "weight_kg": round(wt, 1), "units": units},
        "rows": rows,
    }


def axle_loads(items, key, L):
    """Static 2-support beam axle-load estimate.

    items = [(center_x, weight_kg), ...] for every placed box.
    Returns CG position (% of bed length), the payload carried by the front and
    rear axle groups, % of each axle's limit, and a balance status. Simplified
    planning model (payload contribution only) - verify against a scale for legal use.
    """
    t = TRUCKS.get(key, {})
    ax = t.get("axle")
    W = sum(w for _, w in items)
    if not ax or W <= 0:
        return None
    fx, rx = ax["front_x"], ax["rear_x"]
    span = rx - fx
    cg = sum(cx * w for cx, w in items) / W
    r_rear = sum(w * (cx - fx) for cx, w in items) / span    # moment about front support
    r_front = W - r_rear
    fl, rl = ax["front_limit_kg"], ax["rear_limit_kg"]
    front_pct = round(100 * r_front / fl, 1) if fl else 0
    rear_pct = round(100 * r_rear / rl, 1) if rl else 0
    cg_pct = round(100 * cg / L, 1)

    if r_front > fl:
        status, msg = "over", ax["front_name"] + " overloaded — shift load toward the rear"
    elif r_rear > rl:
        status, msg = "over", ax["rear_name"] + " overloaded — shift load toward the front"
    elif r_front < 0.08 * W or cg > rx:
        status, msg = "warn", "Too rear-heavy — light steer axle; move weight forward"
    elif front_pct > 92 or rear_pct > 92:
        status, msg = "warn", "Approaching an axle limit"
    else:
        status, msg = "ok", "Axle loads balanced and within limits"

    return {
        "cg_pct": cg_pct,
        "front": {"name": ax["front_name"], "kg": round(r_front), "limit": fl, "pct": front_pct},
        "rear": {"name": ax["rear_name"], "kg": round(r_rear), "limit": rl, "pct": rear_pct},
        "status": status, "message": msg,
    }


def load_binding(products, key, placed_volume=None, placed_weight=None):
    """Cube-out vs weight-out for a single rendered truckload."""
    t = TRUCKS[key]
    tv = truck_volume(key)
    vol, wt, _ = portfolio_totals(products)
    vol_pct = round(100 * (placed_volume if placed_volume is not None else vol) / tv, 1) if tv else 0
    w = placed_weight if placed_weight is not None else wt
    wt_pct = round(100 * w / t["payload_kg"], 1) if t["payload_kg"] else 0
    return {"vol_pct": vol_pct, "wt_pct": wt_pct, "weight_kg": round(w, 1),
            "payload_kg": t["payload_kg"],
            "binding": "weight-out" if wt_pct >= vol_pct else "cube-out"}
