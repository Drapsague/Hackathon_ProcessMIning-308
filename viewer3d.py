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
  <div id="msg" style="position:absolute;left:12px;top:10px;font-size:13px;color:#3f3f46;background:#ffffffcc;padding:4px 8px;border-radius:6px">Chargement de la maquette…</div>
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
  msg.textContent = codes.length === 0 ? "Maquette complète"
    : `${{found}} pièce(s) surlignée(s) (${{[...seen].join(", ") || "aucun"}})` +
      (missing.length ? ` · absent de la maquette : ${{missing.join(", ")}}` : "");
}}, (err) => {{ msg.textContent = "Maquette illisible : " + err; }});

function loop() {{ controls.update(); renderer.render(scene, camera); requestAnimationFrame(loop); }}
loop();
window.addEventListener("resize", () => {{
  renderer.setSize(wrap.clientWidth, {height});
  camera.aspect = wrap.clientWidth / {height}; camera.updateProjectionMatrix();
}});
</script>
"""
