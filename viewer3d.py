"""Vue 3D de la maquette (glb) avec surlignage des pièces par code, via three.js."""
from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def glb_base64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def viewer_html(glb_path: str | Path, highlight: list[str], height: int = 460) -> str:
    data = glb_base64(str(glb_path))
    codes = json.dumps(sorted(set(highlight)))
    return f"""
<div id="wrap" style="position:relative;width:100%;height:{height}px;font-family:Helvetica,Arial,sans-serif">
  <div id="msg" style="position:absolute;left:12px;top:10px;font-size:13px;color:#3f3f46;background:#ffffffcc;padding:4px 8px;border-radius:6px">Loading the model…</div>
</div>
<script type="importmap">
{{"imports": {{
  "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
}}}}
</script>
<script type="module">
import * as THREE from "three";
import {{ GLTFLoader }} from "three/addons/loaders/GLTFLoader.js";
import {{ OrbitControls }} from "three/addons/controls/OrbitControls.js";

const codes = {codes};
const wrap = document.getElementById("wrap");
const msg = document.getElementById("msg");
const renderer = new THREE.WebGLRenderer({{ antialias: true, preserveDrawingBuffer: true }});
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(wrap.clientWidth, {height});
renderer.setClearColor(0xfafafa);
wrap.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(1, 2, 1.5);
scene.add(sun);
const camera = new THREE.PerspectiveCamera(40, wrap.clientWidth / {height}, 0.1, 1e6);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

const raw = atob("{data}");
const buf = new Uint8Array(raw.length);
for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);

const hot = new THREE.MeshStandardMaterial({{ color: 0xea580c, emissive: 0x7c2d12, roughness: 0.5 }});
const dim = new THREE.MeshStandardMaterial({{ color: 0xd4d4d8, transparent: true, opacity: 0.28, roughness: 0.8 }});
const base = new THREE.MeshStandardMaterial({{ color: 0xa1a1aa, roughness: 0.6, metalness: 0.2 }});

function codeOf(obj) {{
  // on remonte jusqu'au nœud le plus haut qui porte le code : c'est l'instance de la pièce
  let hit = [null, null];
  for (let o = obj; o; o = o.parent) {{
    const m = (o.name || "").match(/[A-D]\\d{{3}}/);
    if (m && (hit[0] === null || hit[0] === m[0])) hit = [m[0], o.uuid];
    else if (hit[0] !== null) break;
  }}
  return hit;
}}

new GLTFLoader().parse(buf.buffer, "", (gltf) => {{
  const root = gltf.scene;
  root.rotation.x = -Math.PI / 2;  // STEP CATIA en Z vers le haut, three.js en Y
  root.updateMatrixWorld(true);
  const parts = new Set();
  const seen = new Set();
  root.traverse((o) => {{
    if (!o.isMesh) return;
    const [c, owner] = codeOf(o);
    if (codes.length === 0) {{ o.material = base; return; }}
    if (c && codes.includes(c)) {{ o.material = hot; parts.add(owner); seen.add(c); }}
    else o.material = dim;
  }});
  const found = parts.size;
  scene.add(root);
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3()).length();
  const center = box.getCenter(new THREE.Vector3());
  controls.target.copy(center);
  camera.position.copy(center).add(new THREE.Vector3(size * 0.55, size * 0.35, size * 0.6));
  camera.near = size / 1000; camera.far = size * 10; camera.updateProjectionMatrix();
  const missing = codes.filter((c) => !seen.has(c));
  msg.textContent = codes.length === 0 ? "Full model"
    : `${{found}} part(s) highlighted (${{[...seen].join(", ") || "none"}})` +
      (missing.length ? ` · missing from the model: ${{missing.join(", ")}}` : "");
}}, (err) => {{ msg.textContent = "Unreadable model: " + err; }});

function loop() {{ controls.update(); renderer.render(scene, camera); requestAnimationFrame(loop); }}
loop();
window.addEventListener("resize", () => {{
  renderer.setSize(wrap.clientWidth, {height});
  camera.aspect = wrap.clientWidth / {height}; camera.updateProjectionMatrix();
}});
</script>
"""


REPLAY_JS = r"""
<div id="wrap" style="position:relative;width:100%;height:__H__px;font-family:Helvetica,Arial,sans-serif;color:#1f2937">
  <div id="panel" style="position:absolute;left:12px;top:10px;width:330px;background:#ffffffe6;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;font-size:13px;line-height:1.45;z-index:2">
    <div id="op" style="font-weight:700;font-size:14px">Loading the model…</div>
    <div id="det"></div>
    <div id="pcs" style="margin-top:4px"></div>
    <div style="margin-top:6px;height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden"><div id="bar" style="height:8px;width:0;background:#1E3A8A"></div></div>
    <div id="clk" style="font-size:12px;color:#4b5563;margin-top:3px"></div>
  </div>
  <div id="legend" style="position:absolute;right:12px;top:10px;background:#ffffffe6;border:1px solid #e5e7eb;border-radius:10px;padding:8px 10px;font-size:12px;z-index:2"></div>
  <div style="position:absolute;left:12px;right:12px;bottom:10px;display:flex;gap:10px;align-items:center;background:#ffffffe6;border:1px solid #e5e7eb;border-radius:10px;padding:8px 10px;font-size:13px;z-index:2">
    <button id="play" style="font-size:14px;padding:4px 12px;border-radius:6px;border:1px solid #1E3A8A;background:#1E3A8A;color:#fff;cursor:pointer">Play</button>
    <input id="seek" type="range" min="0" max="0" value="0" style="flex:1">
    <span id="pos" style="min-width:70px;text-align:right"></span>
    <select id="speed"><option value="0.5">× 0.5</option><option value="1" selected>× 1</option><option value="2">× 2</option><option value="5">× 5</option></select>
    <select id="mode"><option value="red">In progress: red</option><option value="grav" selected>In progress: delay severity</option><option value="crit">In progress: part criticality</option></select>
  </div>
</div>
<script type="importmap">
{"imports": {"three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
             "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const OPS = __OPS__;
const CRIT = __CRIT__;
const CRIT_COL = {"Critical": "#DC2626", "High": "#F97316", "Medium": "#EAB308", "Low": "#94A3B8"};
const DONE = "#16A34A";
const H = __H__;

// chaque opération occupe sa durée réelle, bout à bout (on ignore les nuits)
let t = 0;
for (const o of OPS) { o.t0 = t; t += o.real; o.t1 = t; }
const TOTAL = t;
// chaque unité consommée par le MES (refs_all garde les doublons) devient une case à remplir
const units = {};
OPS.forEach((o, i) => o.refs_all.forEach(c => (units[c] = units[c] || []).push(i)));

const wrap = document.getElementById("wrap");
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(wrap.clientWidth, H);
renderer.setClearColor(0xf8fafc);
wrap.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(1, 2, 1.5); scene.add(sun);
const camera = new THREE.PerspectiveCamera(40, wrap.clientWidth / H, 0.1, 1e6);
const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;

const mats = {};
function mat(color, opacity = 1) {
  const k = color + opacity;
  if (!mats[k]) mats[k] = new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.1,
    transparent: opacity < 1, opacity, emissive: opacity < 1 ? 0x000000 : new THREE.Color(color).multiplyScalar(0.25) });
  return mats[k];
}
const IDLE = mat("#cbd5e1", 0.22), UNTRACKED = mat("#94a3b8", 0.35);

function codeOf(obj) {
  // remonte jusqu'au nœud le plus haut qui porte le code : c'est l'instance de la pièce
  let hit = null, owner = null;
  for (let o = obj; o; o = o.parent) {
    const m = (o.name || "").match(/[A-D]\d{3}/);
    if (m && (hit === null || hit === m[0])) { hit = m[0]; owner = o; } else if (hit !== null) break;
  }
  return [hit, owner];
}

const meshes = [];
const inCad = new Set();
const raw = atob("__DATA__");
const buf = new Uint8Array(raw.length);
for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);

const $ = id => document.getElementById(id);
const seek = $("seek"); seek.max = OPS.length - 1;
let now = 0, playing = false, lastTs = null;

function activeColor(op, code) {
  const mode = $("mode").value;
  if (mode === "red") return "#DC2626";
  if (mode === "crit") return CRIT_COL[CRIT[code]] || "#DC2626";
  return op.color;
}
function legend() {
  const mode = $("mode").value;
  const sw = c => `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${c};margin-right:5px"></span>`;
  let act = mode === "red" ? sw("#DC2626") + "in progress" :
    mode === "crit" ? Object.entries(CRIT_COL).map(([k, c]) => sw(c) + "in progress, " + k.toLowerCase()).join("<br>") :
    sw("#EAB308") + "in progress, minor delay<br>" + sw("#F97316") + "in progress, major delay<br>" + sw("#DC2626") + "in progress, critical delay";
  $("legend").innerHTML = act + "<br>" + sw(DONE) + "fitted<br>" + sw("#cbd5e1") + "not fitted yet<br>" +
    sw("#94a3b8") + "missing from the MES (e.g. D144)";
}
function idxAt(time) { let k = OPS.findIndex(o => time < o.t1); return k < 0 ? OPS.length - 1 : k; }
const fr = (x, d = 1) => x.toFixed(d);

function render() {
  const k = idxAt(now), op = OPS[k], finished = now >= TOTAL - 1e-6;
  for (const m of meshes) {
    const c = m.userData.code, j = m.userData.op;
    if (!c || j === undefined) { m.material = UNTRACKED; continue; }
    if (finished || j < k) m.material = mat(DONE);
    else if (j === k) m.material = mat(activeColor(op, c));
    else m.material = IDLE;
  }
  const planned = OPS.slice(0, k).reduce((s, o) => s + o.planned, 0) + op.planned * Math.min(1, (now - op.t0) / op.real);
  $("op").textContent = finished ? "Assembly complete: 56 operations" : `Station ${op.poste} · ${op.name}`;
  $("det").innerHTML = finished ? `Actual ${fr(TOTAL / 60)} h for ${fr(OPS.reduce((s, o) => s + o.planned, 0) / 60)} h planned.` :
    `${op.date} · planned ${fr(op.planned, 0)} min, actual ${fr(op.real, 2)} min ` +
    `<b style="color:${op.color}">(+${fr(op.pct, 0)}%, ${op.grav})</b><br>Incident: ${op.alea}`;
  const pcs = op.refs.map(c => `<span style="display:inline-block;margin:1px 3px 1px 0;padding:0 5px;border-radius:4px;border:1px solid #d1d5db;${inCad.has(c) ? "" : "opacity:.55"}">${c}${inCad.has(c) ? "" : " ·not modelled"}</span>`).join("");
  $("pcs").innerHTML = finished ? "" : (op.refs.length ? "Parts: " + pcs : "No part reference (wiring)");
  $("bar").style.width = (100 * now / TOTAL) + "%";
  $("clk").textContent = `Actual time elapsed ${fr(now / 60)} h · equivalent planned time ${fr(planned / 60)} h`;
  $("pos").textContent = `${k + 1} / ${OPS.length}`;
  seek.value = k;
}

function frame(ts) {
  if (playing) {
    if (lastTs !== null) now = Math.min(TOTAL, now + (ts - lastTs) / 40 * parseFloat($("speed").value));
    lastTs = ts;
    if (now >= TOTAL) { playing = false; $("play").textContent = "Replay"; }
    render();
  }
  controls.update(); renderer.render(scene, camera); requestAnimationFrame(frame);
}

$("play").onclick = () => {
  if (now >= TOTAL) now = 0;
  playing = !playing; lastTs = null;
  $("play").textContent = playing ? "Pause" : "Play";
  render();
};
seek.oninput = () => { now = OPS[+seek.value].t0 + 1e-3; playing = false; $("play").textContent = "Play"; render(); };
$("mode").onchange = () => { legend(); render(); };

new GLTFLoader().parse(buf.buffer, "", gltf => {
  const root = gltf.scene;
  root.rotation.x = -Math.PI / 2;
  root.updateMatrixWorld(true);
  const inst = {};  // code -> instances (nœuds) de la maquette
  root.traverse(o => {
    if (!o.isMesh) return;
    const [c, owner] = codeOf(o);
    o.userData.code = c; meshes.push(o);
    if (!c) return;
    inCad.add(c);
    (inst[c] = inst[c] || new Map()).set(owner.uuid, owner);
    o.userData.owner = owner.uuid;
  });
  // les pièces d'origine vont aux premières opérations (côté gauche), les « Symétrie de » ensuite (côté droit)
  const slot = {};
  for (const [c, m] of Object.entries(inst)) {
    const nodes = [...m.values()].sort((a, b) => (a.name.includes("Sym") - b.name.includes("Sym")) || a.name.localeCompare(b.name));
    const u = units[c] || [];
    nodes.forEach((n, j) => { if (u.length) slot[n.uuid] = u[Math.min(j, u.length - 1)]; });
  }
  for (const msh of meshes) if (msh.userData.owner in slot) msh.userData.op = slot[msh.userData.owner];
  scene.add(root);
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3()).length(), center = box.getCenter(new THREE.Vector3());
  controls.target.copy(center);
  camera.position.copy(center).add(new THREE.Vector3(size * 0.55, size * 0.35, size * 0.6));
  camera.near = size / 1000; camera.far = size * 10; camera.updateProjectionMatrix();
  legend(); render();
}, err => { $("op").textContent = "Maquette illisible : " + err; });
requestAnimationFrame(frame);
window.addEventListener("resize", () => {
  renderer.setSize(wrap.clientWidth, H); camera.aspect = wrap.clientWidth / H; camera.updateProjectionMatrix();
});
</script>
"""


def replay_html(glb_path: str | Path, ops: list[dict], criticite: dict[str, str], height: int = 580) -> str:
    """Rejoue les opérations MES dans l'ordre, avec leurs durées réelles, sur la maquette 3D.

    ops : [{poste, name, date, planned, real, pct, grav, color, alea, refs}] dans l'ordre chronologique.
    """
    return (REPLAY_JS.replace("__DATA__", glb_base64(str(glb_path)))
            .replace("__OPS__", json.dumps(ops, ensure_ascii=False))
            .replace("__CRIT__", json.dumps(criticite, ensure_ascii=False))
            .replace("__H__", str(height)))
