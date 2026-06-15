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
TRUCKS = {
    "van":       {"name": "Cargo Van",        "dims": [3.00, 1.70, 1.80], "payload_kg": 1400,  "style": "box"},
    "box16":     {"name": "Box Truck 16 ft",  "dims": [4.90, 2.40, 2.10], "payload_kg": 3000,  "style": "box"},
    "box26":     {"name": "Box Truck 26 ft",  "dims": [7.90, 2.60, 2.70], "payload_kg": 5000,  "style": "box"},
    "cont20":    {"name": "20 ft Container",  "dims": [5.90, 2.35, 2.39], "payload_kg": 21700, "style": "container"},
    "cont40":    {"name": "40 ft Container",  "dims": [12.00, 2.35, 2.39], "payload_kg": 26500, "style": "container"},
    "trailer53": {"name": "53 ft Trailer",    "dims": [16.10, 2.60, 2.90], "payload_kg": 21772, "style": "trailer"},
}
TRUCK_ORDER = ["van", "box16", "box26", "cont20", "cont40", "trailer53"]


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
        "key": key, "name": t["name"], "style": t["style"], "feasible": feasible,
        "trucks": n, "n_vol": n_vol, "n_wt": n_wt, "binding": binding,
        "vol_pct": vol_pct, "wt_pct": wt_pct,
        "payload_kg": t["payload_kg"], "volume_m3": round(tv, 1), "dims": t["dims"],
    }


def recommend(products, pack_eff=PACK_EFF):
    """Recommend the truck that carries the portfolio in the fewest vehicles, then
    with the highest utilization of the binding resource (least wasted capacity)."""
    vol, wt, units = portfolio_totals(products)
    rows = [analyze_truck(products, k, pack_eff) for k in TRUCK_ORDER]
    pool = [r for r in rows if r["feasible"]] or rows

    def score(r):
        bind_u = r["wt_pct"] if r["binding"] == "weight" else r["vol_pct"]
        return (r["trucks"], -bind_u)

    best = sorted(pool, key=score)[0]
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
        "totals": {"volume_m3": round(vol, 2), "weight_kg": round(wt, 1), "units": units},
        "rows": rows,
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
