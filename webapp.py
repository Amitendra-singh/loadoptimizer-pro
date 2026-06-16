"""
webapp.py - Enterprise UI for the truck load-optimizer (zero dependencies, stdlib only).

Run:   python3 webapp.py          then open http://localhost:8765
Renders by calling Blender headless (render_load.py) on your M4 GPU.
Freight analysis (cube-out / weight-out / truck recommendation) is instant via truckspec.
"""

import http.server
import socketserver
import json
import subprocess
import os
import sys
import threading
import time
import urllib.parse

KIT = os.path.dirname(os.path.abspath(__file__))
if KIT not in sys.path:
    sys.path.insert(0, KIT)
import truckspec   # noqa: E402
import packer      # noqa: E402

BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
DRIVER = os.path.join(KIT, "render_load.py")
OUT = os.path.abspath(os.path.join(KIT, "..", "renders", "webapp"))
os.makedirs(OUT, exist_ok=True)
PORT = int(os.environ.get("PORT", "8765"))
_render_lock = threading.Lock()

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LoadOptimizer Pro</title>
<style>
  :root{--bg:#f4f6fa;--panel:#fff;--ink:#16202c;--muted:#6b7787;--line:#e3e8ef;
        --accent:#2563eb;--good:#1e9e62;--warn:#d9534f;--amber:#b9770e;
        --shadow:0 1px 3px rgba(16,32,48,.08),0 1px 2px rgba(16,32,48,.04)}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
  header{background:#0f1b2d;color:#fff;padding:0 22px;height:56px;display:flex;align-items:center;gap:12px}
  header .logo{font-weight:700;font-size:16px;display:flex;align-items:center;gap:9px}
  header .logo b{background:var(--accent);padding:3px 8px;border-radius:7px}
  header .sub{color:#9fb0c4;font-size:12px}.spacer{flex:1}
  header .badge{font-size:11px;color:#9fb0c4;border:1px solid #2b3a4f;padding:4px 10px;border-radius:20px}
  .wrap{display:grid;grid-template-columns:400px 1fr;min-height:calc(100vh - 56px)}
  .side{background:var(--panel);border-right:1px solid var(--line);padding:18px;overflow:auto}
  .main{padding:22px 26px;overflow:auto}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
  .sec{margin-bottom:18px}.sec h3{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
  label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
  input,select{width:100%;background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 9px;font:inherit;color:var(--ink)}
  input[type=color]{padding:2px;height:34px;cursor:pointer}
  .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
  .chips{display:flex;gap:8px;flex-wrap:wrap}
  .chip{display:flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:13px}
  .chip input{width:auto}
  table.products{width:100%;border-collapse:collapse}
  table.products th{font-size:11px;color:var(--muted);text-align:left;font-weight:600;padding:2px 3px}
  table.products td{padding:2px}table.products td input{padding:5px 5px}
  .x{border:none;background:none;color:var(--muted);font-size:16px;cursor:pointer;width:auto}.x:hover{color:var(--warn)}
  .btn{display:block;width:100%;border:none;border-radius:9px;padding:12px;font-weight:600;font-size:14px;cursor:pointer}
  .btn.primary{background:var(--accent);color:#fff}.btn.primary:disabled{opacity:.55;cursor:default}
  .btn.alt{background:#0f1b2d;color:#fff;margin-bottom:10px}
  .btn.ghost{background:#fff;border:1px dashed var(--line);color:var(--muted);padding:8px;font-weight:500}
  .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
  .kpi{padding:16px}.kpi .n{font-size:24px;font-weight:700}.kpi .l{color:var(--muted);font-size:12px;margin-top:2px}
  .kpi.good .n{color:var(--good)}.kpi.warn .n{color:var(--warn)}
  .rec{padding:16px 18px;margin-bottom:18px;border-left:4px solid var(--accent)}
  .rec .h{display:flex;align-items:center;gap:10px;margin-bottom:4px}
  .rec .h b{font-size:17px}.rec .reason{color:var(--muted);font-size:13px}
  .pill{font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:.4px}
  .pill.cube{background:#e6f1fb;color:#185fa5}.pill.weight{background:#faeeda;color:#854f0b}.pill.bal{background:#e1f5ee;color:#0f6e56}
  .fill{padding:18px;margin-bottom:18px}.fill .top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
  .fill .top b{font-size:14px}.fill .top span{color:var(--muted);font-size:12px}
  .bar{height:22px;border-radius:7px;background:#eef1f6;overflow:hidden;position:relative;border:1px solid var(--line);margin-bottom:12px}
  .bar>i{display:block;height:100%;width:0;border-radius:7px 0 0 7px;transition:width 1.1s cubic-bezier(.2,.7,.2,1)}
  .bar.vol>i{background:var(--accent)}.bar.wt>i{background:var(--amber)}
  .bar>em{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-style:normal;font-weight:700;font-size:12px;color:#0f1b2d}
  .gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-bottom:18px}
  figure{margin:0;overflow:hidden;border-radius:12px;border:1px solid var(--line);background:#fff;box-shadow:var(--shadow)}
  figure img,figure video{width:100%;display:block}figcaption{padding:8px 12px;color:var(--muted);font-size:12px;text-transform:capitalize}
  table.bd{width:100%;border-collapse:collapse;font-size:13px}
  table.bd th{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.4px;padding:8px 10px;border-bottom:1px solid var(--line)}
  table.bd td{padding:8px 10px;border-bottom:1px solid var(--line)}
  table.bd td.bad{color:var(--warn);font-weight:600}table.bd td.ok{color:var(--good)}
  table.bd tr.best{background:#eef5ff}
  .sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:7px;vertical-align:-1px;border:1px solid rgba(0,0,0,.12)}
  .empty{height:60vh;display:flex;align-items:center;justify-content:center;color:var(--muted);text-align:center}
  .spin{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:s 1s linear infinite;margin:0 auto 14px}
  @keyframes s{to{transform:rotate(360deg)}}.hint{color:var(--muted);font-size:11px;margin-top:6px}
  .axle{padding:18px;margin-bottom:18px}
  .axtop{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
  .axtop b{font-size:14px}
  .apill{font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:.4px}
  .apill.okp{background:#e1f5ee;color:#0f6e56}.apill.warnp{background:#faeeda;color:#854f0b}.apill.overp{background:#fcebeb;color:#a32d2d}
  .cgwrap{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .cgend{font-size:11px;color:var(--muted);white-space:nowrap}
  .cgbar{position:relative;flex:1;height:14px;border-radius:7px;background:#eef1f6;border:1px solid var(--line)}
  .cgzone{position:absolute;left:38%;width:24%;top:0;bottom:0;background:#e1f5ee;border-left:1px dashed #1e9e62;border-right:1px dashed #1e9e62}
  .cgmark{position:absolute;top:-4px;width:4px;height:22px;border-radius:2px;background:#0f1b2d;transform:translateX(-2px)}
  .cgcap{font-size:12px;color:var(--muted);margin-bottom:14px}
  .axrow{display:grid;grid-template-columns:130px 1fr 210px;gap:10px;align-items:center;margin-bottom:8px}
  .axname{font-size:13px}.axbar{height:16px;border-radius:6px;background:#eef1f6;border:1px solid var(--line);overflow:hidden}
  .axbar>i{display:block;height:100%}.axval{font-size:12px;color:var(--muted);text-align:right}
  .axmsg{margin-top:10px;font-size:13px}.axmsg.over{color:var(--warn)}.axmsg.warn{color:var(--amber)}.axmsg.ok{color:var(--good)}
  .rec-h{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}
  .rec-h b{font-size:15px}.hint2{color:var(--muted);font-size:12px}
  .recopt{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px}
  .recopt.best{border-color:var(--accent);background:#f5f9ff}
  .recopt-main{display:flex;justify-content:space-between;align-items:center}
  .recopt-main b{font-size:14px}
  .recopt-sub{color:var(--muted);font-size:12px;margin-top:4px}
  .recbadge{font-size:10px;font-weight:600;background:var(--accent);color:#fff;padding:2px 8px;border-radius:20px;text-transform:uppercase;letter-spacing:.4px;margin-left:8px}
  .recopt-bars{display:flex;gap:18px;margin-top:10px}
  .rb{display:flex;align-items:center;gap:8px;flex:1;font-size:12px;color:var(--muted)}
  .rb .rbbar{flex:1;height:8px;border-radius:5px;background:#eef1f6;overflow:hidden}
  .rb .rbbar>i{display:block;height:100%;background:var(--accent)}.rb .rbbar.wt>i{background:var(--amber)}
  .recall{margin-top:6px}.recall summary{cursor:pointer;color:var(--accent);font-size:13px}
  .seltruck{font-size:13px;color:var(--muted);margin:4px 0 12px}.seltruck b{color:var(--ink)}
  .recopt{cursor:pointer}.recopt:hover{border-color:var(--accent)}
  table.bd tr.drow{cursor:pointer}table.bd tr.drow:hover{background:#eef5ff}
  .drill{padding:16px 18px;margin-top:12px;border:1px solid var(--accent)!important}
  .drill-h{display:flex;align-items:center;gap:10px;margin-bottom:12px}.drill-h b{font-size:14px}
  .drillkpi{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:12px;font-size:13px;color:var(--muted)}
  .lefthi{color:var(--warn)}
</style></head>
<body>
<header><div class="logo"><b>LO</b> LoadOptimizer <span style="font-weight:400">Pro</span></div>
  <div class="sub">3D bin-packing &middot; cube/weight-out analysis &middot; photoreal on Apple M4</div>
  <div class="spacer"></div><div class="badge">● engine ready</div></header>
<div class="wrap">
  <div class="side">
    <div class="sec"><h3>Vehicle</h3>
      <label>Truck / container type</label>
      <select id="truck" onchange="applyTruck()"></select>
      <div class="grid3" style="margin-top:10px">
        <div><label>Length m</label><input id="cl" type="number" step="0.1"></div>
        <div><label>Width m</label><input id="cw" type="number" step="0.1"></div>
        <div><label>Height m</label><input id="ch" type="number" step="0.1"></div></div>
      <div class="hint" id="payhint"></div>
    </div>
    <div class="sec"><h3>Output</h3>
      <label>Quality</label>
      <select id="q" style="margin-bottom:10px"><option>draft</option><option>fast</option><option selected>balanced</option><option>high</option></select>
      <label>Views &amp; loading guide</label>
      <div class="chips">
        <label class="chip"><input type="checkbox" class="v" value="hero" checked>Truck hero</label>
        <label class="chip"><input type="checkbox" class="v" value="top" checked>Top plan</label>
        <label class="chip"><input type="checkbox" class="v" value="rear">Rear</label>
        <label class="chip"><input type="checkbox" class="v" value="axle">Axle view</label>
        <label class="chip"><input type="checkbox" id="anim">🎬 Loading animation</label>
        <label class="chip"><input type="checkbox" id="balance">⚖ Balance axle load</label></div>
    </div>
    <div class="sec"><h3>Products</h3>
      <table class="products"><thead><tr><th>SKU</th><th>L</th><th>W</th><th>H</th><th>Qty</th><th>kg</th><th>Color</th><th></th></tr></thead>
        <tbody id="rows"></tbody></table>
      <div class="chips" style="margin-top:10px">
        <button class="btn ghost" onclick="addRow()">+ Add SKU</button>
        <button class="btn ghost" onclick="document.getElementById('csv').click()">⭱ CSV</button>
        <input id="csv" type="file" accept=".csv" style="display:none" onchange="importCSV(this.files[0])"></div>
      <div class="hint">CSV: label,l,w,h,count,weight (optional r,g,b)</div>
    </div>
    <button class="btn alt" onclick="analyze()">⚖ Recommend best truck</button>
    <button class="btn primary" id="go" onclick="optimize()">Optimize &amp; Render</button>
  </div>
  <div class="main" id="out"><div class="empty">Configure a load, then <b style="color:var(--ink);margin:0 4px">Recommend</b> or <b style="color:var(--ink);margin:0 4px">Render</b></div></div>
</div>
<script>
let TRUCKMETA={}, ORDER=[];
async function initTrucks(){
  const d=await (await fetch('/api/trucks')).json();
  TRUCKMETA=d.trucks; ORDER=d.order;
  const sel=document.getElementById('truck');
  ORDER.forEach(k=>{const o=document.createElement('option');o.value=k;
    o.textContent=TRUCKMETA[k].name+'  ·  '+TRUCKMETA[k].payload_kg.toLocaleString()+' kg';sel.appendChild(o);});
  sel.value='box16'; applyTruck();
}
function applyTruck(){
  const k=document.getElementById('truck').value, t=TRUCKMETA[k];
  cl.value=t.dims[0]; cw.value=t.dims[1]; ch.value=t.dims[2];
  document.getElementById('payhint').textContent=
    'Payload '+t.payload_kg.toLocaleString()+' kg · volume '+(t.dims[0]*t.dims[1]*t.dims[2]).toFixed(1)+' m³';
}
const DEF=[
  ["pallet-box",0.60,0.50,0.55,30,25,"#5a3a1c"],["carton-M",0.45,0.40,0.40,60,12,"#7a512c"],
  ["carton-S",0.40,0.30,0.30,80,6,"#664021"],["appliance",0.62,0.60,0.82,14,40,"#4d2c14"],
  ["retail-white",0.44,0.40,0.50,30,10,"#b3aea1"],["fragile-blue",0.50,0.50,0.34,20,8,"#1a3d75"]];
let rows=DEF.map(r=>r.slice());
function renderRows(){
  document.getElementById('rows').innerHTML=rows.map((r,i)=>`<tr>
    <td><input value="${r[0]}" oninput="rows[${i}][0]=this.value"></td>
    <td><input type="number" step="0.05" value="${r[1]}" oninput="rows[${i}][1]=+this.value"></td>
    <td><input type="number" step="0.05" value="${r[2]}" oninput="rows[${i}][2]=+this.value"></td>
    <td><input type="number" step="0.05" value="${r[3]}" oninput="rows[${i}][3]=+this.value"></td>
    <td><input type="number" value="${r[4]}" oninput="rows[${i}][4]=+this.value"></td>
    <td><input type="number" step="0.5" value="${r[5]}" oninput="rows[${i}][5]=+this.value"></td>
    <td><input type="color" value="${r[6]}" oninput="rows[${i}][6]=this.value"></td>
    <td><button class="x" onclick="rows.splice(${i},1);renderRows()">&times;</button></td></tr>`).join('');
}
function addRow(){rows.push(["sku",0.4,0.3,0.3,10,5,"#664021"]);renderRows();}
renderRows();
function hex2rgb(h){return [parseInt(h.slice(1,3),16)/255,parseInt(h.slice(3,5),16)/255,parseInt(h.slice(5,7),16)/255];}
function rgb2hex(r,g,b){const f=v=>('0'+Math.round(v*255).toString(16)).slice(-2);return '#'+f(r)+f(g)+f(b);}
function importCSV(file){if(!file)return;const rd=new FileReader();rd.onload=e=>{
  const L=e.target.result.split(/\r?\n/).filter(x=>x.trim());const h=L.shift().split(',').map(s=>s.trim().toLowerCase());const ix=n=>h.indexOf(n);
  rows=L.map(l=>{const c=l.split(',');const col=(ix('r')>=0&&c[ix('r')]!=='')?rgb2hex(+c[ix('r')],+c[ix('g')],+c[ix('b')]):"#664021";
    return [c[ix('label')]||'sku',+c[ix('l')],+c[ix('w')],+c[ix('h')],+c[ix('count')],ix('weight')>=0?+c[ix('weight')]:0,col];});renderRows();};rd.readAsText(file);}
function products(){return rows.map(r=>({label:r[0],l:r[1],w:r[2],h:r[3],count:r[4],weight:r[5],color:hex2rgb(r[6])}));}

function bpill(b){const c={cube:'cube',weight:'weight',even:'bal'}[b];
  const t={cube:'cube-out',weight:'weight-out',even:'balanced'}[b];return `<span class="pill ${c}">${t}</span>`;}
function recCard(rec){
  const t=rec.totals;
  const opts=rec.shortlist.map((s,i)=>`
    <div class="recopt ${i===0?'best':''}" onclick="drillDown('${s.key}')">
      <div class="recopt-main">
        <div><b>${s.name}</b>${i===0?'<span class="recbadge">best fit</span>':''}
          <div class="recopt-sub">${s.class} · ${s.trucks} truck${s.trucks>1?'s':''} · ${bpill(s.binding)} · <span style="color:var(--accent)">view fit detail →</span></div></div>
        <button class="btn ghost" style="width:auto;padding:6px 12px" onclick="event.stopPropagation();useTruck('${s.key}')">Use this</button></div>
      <div class="recopt-bars">
        <div class="rb"><span>Volume</span><div class="rbbar"><i style="width:${Math.min(100,s.vol_pct)}%"></i></div><span>${s.vol_pct}%</span></div>
        <div class="rb"><span>Payload</span><div class="rbbar wt"><i style="width:${Math.min(100,s.wt_pct)}%"></i></div><span>${s.wt_pct}%</span></div>
      </div></div>`).join('');
  const tbl=rec.rows.map(r=>`<tr class="drow ${r.key===rec.recommended?'best':''}" onclick="drillDown('${r.key}')">
      <td>${r.name}${r.feasible?'':' ⚠'}</td><td>${r.class}</td><td>${r.trucks}</td>
      <td>${r.vol_pct}%</td><td>${r.wt_pct}%</td><td>${bpill(r.binding)}</td></tr>`).join('');
  return `<div class="card rec">
    <div class="rec-h"><b>Recommended vehicles for this shipment</b>
      <span class="hint2">all ${t.units} units · ${t.volume_m3} m³ · ${t.weight_kg.toLocaleString()} kg</span></div>
    ${opts}
    <details class="recall"><summary>Compare all vehicles — click any row for fit detail (no render)</summary>
      <table class="bd" style="margin-top:8px"><thead><tr><th>Vehicle</th><th>Type</th><th>Trucks</th><th>Volume</th><th>Payload</th><th>Binds on</th></tr></thead><tbody>${tbl}</tbody></table>
      <div class="hint">% is per truck across the number of trucks needed to ship the whole order.</div>
    </details>
    <div id="drillpanel"></div></div>`;
}
let drillCache={};
function bpill2(b){const c=b==='weight-out'?'weight':(b==='cube-out'?'cube':'bal');return `<span class="pill ${c}">${b}</span>`;}
async function drillDown(key){
  let panel=document.getElementById('drillpanel'); if(!panel) return;
  if(drillCache[key]){panel.innerHTML=renderDrill(drillCache[key],key);panel.scrollIntoView({behavior:'smooth',block:'nearest'});return;}
  panel.innerHTML='<div class="card drill"><div class="spin"></div></div>';
  try{
    const d=await (await fetch('/api/breakdown',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({truck_key:key,balance:document.getElementById('balance').checked,products:products()})})).json();
    if(!d.ok){panel.innerHTML='<div class="card drill" style="color:var(--warn)">Could not compute: '+(d.error||'')+'</div>';return;}
    drillCache[key]=d; panel.innerHTML=renderDrill(d,key); panel.scrollIntoView({behavior:'smooth',block:'nearest'});
  }catch(e){panel.innerHTML='<div class="card drill" style="color:var(--warn)">Error: '+e+'</div>';}
}
function renderDrill(d,key){
  const s=d.stats,fit=s.leftover===0,ax=s.axle;
  const rows=(s.breakdown||[]).map(b=>`<tr><td>${b.label}</td><td>${b.requested}</td><td>${b.placed}</td>
    <td class="${b.left>0?'bad':'ok'}">${b.left>0?b.left:'✓ all'}</td></tr>`).join('');
  return `<div class="card drill">
    <div class="drill-h"><b>${d.truck_name} — fit detail</b><span class="hint2">computed without rendering</span>
      <button class="x" style="margin-left:auto" onclick="document.getElementById('drillpanel').innerHTML=''">&times;</button></div>
    <div class="drillkpi">
      <span><b style="color:var(--good)">${s.utilization}%</b> volume</span>
      <span><b style="color:var(--amber)">${s.wt_pct}%</b> payload</span>
      <span>${bpill2(s.binding)}</span>
      <span><b>${s.placed}</b> loaded · <b class="${fit?'':'lefthi'}">${s.leftover}</b> didn't fit</span>
      ${ax?`<span>axle <b style="color:${ax.status==='ok'?'var(--good)':'var(--warn)'}">${ax.status}</b></span>`:''}</div>
    <table class="bd"><thead><tr><th>SKU</th><th>Requested</th><th>Loaded</th><th>Didn't fit</th></tr></thead><tbody>${rows}</tbody></table>
    <button class="btn ghost" style="margin-top:12px;width:auto;padding:7px 14px" onclick="useTruck('${key}')">Select this vehicle</button>
  </div>`;
}
function useTruck(k){document.getElementById('truck').value=k;applyTruck();}

let lastRender=null;
function backBar(){return '<button class="btn ghost" style="margin-bottom:14px;width:auto;padding:8px 16px" onclick="restoreRender()">← Back to the render &amp; all views</button>';}
function restoreRender(){ if(lastRender){drillCache={};renderResults(lastRender.d,lastRender.views,lastRender.spec);} }
async function analyze(){
  document.getElementById('out').innerHTML='<div class="empty"><div class="spin"></div></div>';
  const rec=await (await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({products:products()})})).json();
  document.getElementById('out').innerHTML=(lastRender?backBar():'')+recCard(rec);
}

async function optimize(){
  const views=[...document.querySelectorAll('.v:checked')].map(c=>c.value);
  const spec={container:[+cl.value,+cw.value,+ch.value],truck_key:document.getElementById('truck').value,
    quality:q.value,resolution:[1500,1050],views,animate:document.getElementById('anim').checked,
    balance:document.getElementById('balance').checked,
    anim_resolution:[1280,720],stagger:2,products:products()};
  const go=document.getElementById('go');go.disabled=true;go.textContent='Rendering…';
  document.getElementById('out').innerHTML='<div class="empty"><div><div class="spin"></div>Optimizing pack &amp; path-tracing on the GPU…<br><small>first run boots Blender (~a few seconds)</small></div></div>';
  try{
    const d=await (await fetch('/api/pack',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)})).json();
    if(!d.ok){document.getElementById('out').innerHTML='<div class="empty">Render failed<br><pre>'+(d.error||'')+'</pre></div>';}
    else { lastRender={d:d,views:views,spec:spec}; drillCache={}; renderResults(d,views,spec); }
  }catch(e){document.getElementById('out').innerHTML='<div class="empty">Error: '+e+'</div>';}
  go.disabled=false;go.textContent='Optimize & Render';
}

function renderResults(d,views,spec){
  const s=d.stats,fit=s.leftover===0;
  const bcls=s.binding==='weight-out'?'weight':'cube';
  const media=(d.video?`<figure><video src="${d.video}" controls autoplay loop muted playsinline></video><figcaption>🎬 loading sequence — order &amp; placement</figcaption></figure>`:'')+
    views.map(v=>d.images[v]?`<figure><img src="${d.images[v]}"><figcaption>${v} view</figcaption></figure>`:'').join('');
  const bd=(s.breakdown||[]).map((b,i)=>{const col=rgb2hex(...spec.products[i].color);
    return `<tr><td><span class="sw" style="background:${col}"></span>${b.label}</td><td>${b.requested}</td><td>${b.placed}</td>
      <td class="${b.left>0?'bad':'ok'}">${b.left>0?b.left:'✓ all'}</td></tr>`;}).join('');
  const tname=(TRUCKMETA[spec.truck_key]||{}).name||'selected truck';
  document.getElementById('out').innerHTML=
    (d.recommendation?recCard(d.recommendation):'')+
    `<div class="seltruck">On your selected vehicle — <b>${tname}</b> (one truck)</div>
    <div class="kpis">
      <div class="card kpi good"><div class="n">${s.utilization}%</div><div class="l">Space utilized</div></div>
      <div class="card kpi"><div class="n">${s.wt_pct}%</div><div class="l">Payload used (${(s.weight_kg||0).toLocaleString()} kg)</div></div>
      <div class="card kpi"><div class="n">${s.placed}</div><div class="l">Units loaded</div></div>
      <div class="card kpi ${fit?'':'warn'}"><div class="n">${s.leftover}</div><div class="l">Didn't fit</div></div></div>
    <div class="card fill">
      <div class="top"><b>This truck — <span class="pill ${bcls}">${s.binding}</span></b><span>${s.used_vol} m³ used · ${(s.weight_kg||0).toLocaleString()} of ${(s.payload_kg||0).toLocaleString()} kg · ${s.overlaps} overlaps</span></div>
      <div class="bar vol"><i id="bv"></i><em>${s.utilization}% volume</em></div>
      <div class="bar wt"><i id="bw"></i><em>${s.wt_pct}% payload</em></div>
      ${fit?'':'<div class="hint" style="color:var(--warn)">⚠ '+s.leftover+" unit(s) left over — use the recommended vehicle above or split the shipment.</div>"}
    </div>
    ${s.axle?renderAxle(s.axle):''}
    <div class="gallery">${media}</div>
    <div class="card" style="padding:4px 6px 6px"><table class="bd"><thead><tr><th>SKU</th><th>Requested</th><th>Loaded</th><th>Not loaded</th></tr></thead><tbody>${bd}</tbody></table></div>`;
  requestAnimationFrame(()=>{document.getElementById('bv').style.width=Math.min(100,s.utilization)+'%';
    document.getElementById('bw').style.width=Math.min(100,s.wt_pct||0)+'%';});
}
function renderAxle(a){
  const pill={ok:'okp',warn:'warnp',over:'overp'}[a.status];
  const pillt={ok:'within limits',warn:'check balance',over:'over limit'}[a.status];
  const bar=o=>{const c=o.pct>100?'#d9534f':(o.pct>92?'#b9770e':'#1e9e62');
    return `<div class="axrow"><div class="axname">${o.name}</div>
      <div class="axbar"><i style="width:${Math.max(0,Math.min(100,o.pct))}%;background:${c}"></i></div>
      <div class="axval">${o.kg.toLocaleString()} / ${o.limit.toLocaleString()} kg <b style="color:${c}">${o.pct}%</b></div></div>`;};
  return `<div class="card axle">
    <div class="axtop"><b>Axle weight distribution</b><span class="apill ${pill}">${pillt}</span></div>
    <div class="cgwrap"><span class="cgend">Front · cab</span>
      <div class="cgbar"><span class="cgzone"></span><span class="cgmark" style="left:${Math.max(0,Math.min(100,a.cg_pct))}%"></span></div>
      <span class="cgend">Rear · door</span></div>
    <div class="cgcap">Center of gravity at <b>${a.cg_pct}%</b> of bed length (green band = balanced zone)</div>
    ${bar(a.front)}${bar(a.rear)}
    <div class="axmsg ${a.status}">${a.message}</div>
  </div>`;
}
initTrucks();
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, "application/json", json.dumps(obj).encode())

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
        elif path == "/api/trucks":
            self._json({"order": truckspec.TRUCK_ORDER, "trucks": truckspec.TRUCKS})
        elif path.startswith("/img/"):
            fp = os.path.join(OUT, os.path.basename(path))
            if os.path.exists(fp):
                ctype = "video/mp4" if fp.endswith(".mp4") else "image/png"
                with open(fp, "rb") as f:
                    self._send(200, ctype, f.read())
            else:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if path == "/api/analyze":
            self._json(truckspec.recommend(body.get("products", [])))
            return
        if path == "/api/breakdown":
            key = body.get("truck_key")
            cont = (truckspec.TRUCKS[key]["dims"] if key in truckspec.TRUCKS
                    else body.get("container"))
            try:
                _, stats = packer.load_stats(tuple(cont), body.get("products", []),
                                             truck_key=key, balance=bool(body.get("balance")))
                self._json({"ok": True, "stats": stats,
                            "truck_name": truckspec.TRUCKS.get(key, {}).get("name", key)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if path != "/api/pack":
            self._send(404, "text/plain", b"not found")
            return
        sp = os.path.join(OUT, "spec.json")
        with open(sp, "w") as f:
            json.dump(body, f)
        rp = os.path.join(OUT, "report.json")
        if os.path.exists(rp):
            os.remove(rp)
        t0 = time.time()
        with _render_lock:
            proc = subprocess.run([BLENDER, "--background", "--python", DRIVER, "--", sp, OUT],
                                  capture_output=True, text=True, timeout=1200)
        if os.path.exists(rp):
            with open(rp) as f:
                report = json.load(f)
            stamp = int(time.time())
            imgs = {v: f"/img/{os.path.basename(p)}?t={stamp}" for v, p in report["images"].items()}
            out = {"ok": True, "stats": report["stats"], "images": imgs,
                   "recommendation": report.get("recommendation"),
                   "seconds": round(time.time() - t0, 1)}
            if report.get("video"):
                out["video"] = f"/img/{os.path.basename(report['video'])}?t={stamp}"
        else:
            out = {"ok": False, "error": (proc.stderr or proc.stdout)[-1500:]}
        self._json(out)

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as httpd:
        print(f"LoadOptimizer Pro -> http://localhost:{PORT}  (Ctrl-C to stop)")
        httpd.serve_forever()
