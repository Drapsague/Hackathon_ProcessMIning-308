"""Passe l'atelier 3D (avion_3d.html) en anglais et retire les données personnelles qu'il embarquait.

    python tools/translate_avion3d.py

Les libellés MES et PLM sont repris des fichiers UK de data/ ; la liste ERP (prénoms, noms, matricules), que
le script de l'atelier n'utilise pas, est supprimée.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etl import CRITICALITY_EN, DATA_DIR, load_mes, load_plm  # noqa: E402

HTML = ROOT / "avion_3d.html"

GROUPES = [  # du plus long au plus court, pour ne pas couper un nom composé
    ("Train d'atterrissage avant", "Front landing gear"), ("Corps / Cabines", "Body / Cabins"),
    ("Ailerons gauche", "Left fins"), ("Aileron de queue", "Tail fin"), ("Symétrie de ", "Mirror of "),
    ("modifié", "modified"), ("Ailes", "Wings"), ("Nez", "Nose"),
]

INTERFACE = [
    ('<span class="subtitle">Assembly lab / jumeau visuel</span>', '<span class="subtitle">Assembly lab / visual twin</span>'),
    ("pièces CAO · modèle STEP d'origine", "CAD parts · original STEP model"),
    ('aria-label="Avion en 3D : cliquer sur une pièce pour l\'extraire"', 'aria-label="3D aircraft: click a part to pull it out"'),
    ("<p>Préparation des pièces et de leurs positions…</p>", "<p>Preparing parts and their positions…</p>"),
    ('title="Échap : réassembler">Réassembler</button>', 'title="Esc: reassemble">Reassemble</button>'),
    ('title="Cadrer le modèle">Cadrer</button>', 'title="Frame the model">Frame</button>'),
    ('<button id="rotate">Rotation auto</button>', '<button id="rotate">Auto rotate</button>'),
    ('aria-label="Mode de couleur"', 'aria-label="Colour mode"'),
    ('<option value="cad">Couleurs CAO</option>', '<option value="cad">CAD colours</option>'),
    ('<option value="production">Production · écarts MES</option>', '<option value="production">Production · MES gaps</option>'),
    ('<option value="criticality">Criticité PLM</option>', '<option value="criticality">PLM criticality</option>'),
    ('title="Exporter une image PNG">Photo</button>', 'title="Export a PNG image">Photo</button>'),
    ('title="Plein écran"', 'title="Full screen"'),
    ("<div class=\"tag\">MODÈLE D'ASSEMBLAGE / 99 COMPOSANTS</div>", '<div class="tag">ASSEMBLY MODEL / 99 COMPONENTS</div>'),
    ("<h1>Chaque pièce<br>a sa place.</h1><p>Cliquez pour extraire.<br>Cliquez à nouveau pour réassembler.</p>",
     "<h1>Every part<br>in its place.</h1><p>Click to pull out.<br>Click again to put back.</p>"),
    ("Glisser : tourner · molette : zoomer<br>Clic droit : déplacer · Échap : réassembler",
     "Drag: rotate · wheel: zoom<br>Right click: pan · Esc: reassemble"),
    ('<label for="explode">Vue éclatée</label>', '<label for="explode">Exploded view</label>'),
    ('aria-label="Éclatement global"', 'aria-label="Global explosion"'),
    ('<button id="ghost">Transparence</button>', '<button id="ghost">Transparency</button>'),
    ('<span class="tip" id="sceneStatus">Assemblage complet</span>', '<span class="tip" id="sceneStatus">Fully assembled</span>'),
    ('<p class="eyebrow">Pièce sélectionnée</p>', '<p class="eyebrow">Selected part</p>'),
    ('aria-label="Sélectionner une pièce"', 'aria-label="Select a part"'),
    ("Choisir une pièce…", "Choose a part…"),
    ("Cliquez sur une pièce de l’avion pour la sortir et voir ses informations.",
     "Click a part of the aircraft to pull it out and see its details."),
    ("<summary>Pièces extraites ", "<summary>Extracted parts "),
    ("Aucune pièce extraite.", "No part extracted."),
    ("<summary>Rechercher et filtrer</summary>", "<summary>Search and filter</summary>"),
    ('placeholder="Référence ou nom…" aria-label="Rechercher une pièce"', 'placeholder="Reference or name…" aria-label="Search a part"'),
    ('aria-label="Sous-assemblage"', 'aria-label="Sub-assembly"'),
    ("Tous les sous-assemblages", "All sub-assemblies"),
    ("<summary>Aide et sources</summary>", "<summary>Help and sources</summary>"),
    ("Cliquez pour extraire, recliquez pour remettre. « Réassembler » restitue toutes les positions. Glissez pour "
     "tourner et utilisez la molette pour zoomer.",
     "Click to pull out, click again to put back. \"Reassemble\" restores every position. Drag to rotate and use the "
     "wheel to zoom."),
    ("Géométrie issue du STEP fourni. Les références absentes des noms CAO ne sont pas inventées.",
     "Geometry from the supplied STEP file. References missing from the CAD names are not made up."),
    ("Production : couleur selon le plus grand écart relatif des opérations liées. Cela n’identifie pas une pièce en "
     "panne. Les criticités et coûts proviennent du PLM fourni.",
     "Production: colour from the largest relative gap of the linked operations. It does not identify a faulty part. "
     "Criticalities and costs come from the supplied PLM."),
    # script
    ("new Intl.NumberFormat('fr-FR'", "new Intl.NumberFormat('en-GB'"),
    ("o.anomalie.includes('Durée')", "o.anomalie.includes('Duration')"),
    ("const criticality={Critique:0xe17a68,Haute:0xe7b56b,Moyenne:0x79c2c5,Basse:0x879cb9};",
     "const criticality={Critical:0xe17a68,High:0xe7b56b,Medium:0x79c2c5,Low:0x879cb9};"),
    ("' pièces visibles sur '", "' parts visible out of '"),
    ("Couleurs d’origine du modèle CAO", "Original CAD model colours"),
    ("Pièce extraite / sélectionnée", "Part extracted / selected"),
    ("Écart relatif max. des opérations liées à la référence", "Max relative gap of the operations linked to the reference"),
    ("25–40 %", "25–40%"), ("≤ 25 %", "≤ 25%"), ("> 40 %", "> 40%"),
    ("Non reliée", "Not linked"),
    ("title=\"Remettre ${escapeHTML(m.userData.part.name)}\"", "title=\"Put back ${escapeHTML(m.userData.part.name)}\""),
    ("list.length+' pièce'+(list.length>1?'s':'')+' extraite'+(list.length>1?'s':''):explode>0?'Vue éclatée':'Assemblage complet'",
     "list.length+' part'+(list.length>1?'s':'')+' extracted':explode>0?'Exploded view':'Fully assembled'"),
    ("escapeHTML(p.reference||'Pièce CAO')", "escapeHTML(p.reference||'CAD part')"),
    ("${isOut?'Pièce extraite':'Pièce en place'}", "${isOut?'Part extracted':'Part in place'}"),
    ("' · Criticité '", "' · Criticality '"),
    ("${isOut?'Remettre la pièce':'Extraire la pièce'}", "${isOut?'Put the part back':'Pull the part out'}"),
    ('<button id="focusPart">Cadrer</button>', '<button id="focusPart">Frame</button>'),
    ("<summary>Données et opérations", "<summary>Data and operations"),
    ("<small>Coût unitaire PLM</small><b>${num(meta.cout_unitaire_eur)} €</b>", "<small>PLM unit cost</small><b>€${num(meta.cout_unitaire_eur)}</b>"),
    ("<small>Délai de référence</small>", "<small>Reference lead time</small>"),
    ("Fournisseur : ${escapeHTML(meta.fournisseur)}", "Supplier: ${escapeHTML(meta.fournisseur)}"),
    ("Aucune fiche PLM reliée automatiquement.", "No PLM record linked automatically."),
    ("<h3>Opérations associées (${ops.length})</h3>", "<h3>Related operations (${ops.length})</h3>"),
    ("min prévues / ${num(o.reel_declare_min)} min déclarées", "min planned / ${num(o.reel_declare_min)} min declared"),
    ("Écart : +${num(o.ecart_min)} min (${num(o.ecart_pct*100)} %)", "Gap: +${num(o.ecart_min)} min (${num(o.ecart_pct*100)}%)"),
    ("Horaires incohérents : durée à valider.", "Inconsistent times: duration to be validated."),
    ("Aucune opération MES reliée.", "No linked MES operation."),
    ("Une référence peut servir à plusieurs opérations. La couleur ne désigne pas une pièce en panne.",
     "A reference can be used by several operations. The colour does not point to a faulty part."),
    ("Instance CAO : ${escapeHTML(p.name)}<br>Identifiant : ${p.id}", "CAD instance: ${escapeHTML(p.name)}<br>Identifier: ${p.id}"),
    ("$('explodeValue').textContent='0 %'", "$('explodeValue').textContent='0%'"),
    ("Math.round(explode*100)+' %'", "Math.round(explode*100)+'%'"),
    ("<output id=\"explodeValue\">0 %</output>", "<output id=\"explodeValue\">0%</output>"),
    ("(m.userData.part.reference||'Pièce CAO')", "(m.userData.part.reference||'CAD part')"),
    ("'MODÈLE D’ASSEMBLAGE / '+meshes.length+' COMPOSANTS'", "'ASSEMBLY MODEL / '+meshes.length+' COMPONENTS'"),
    ("'Le visualiseur 3D n’a pas démarré : '+event.message+'. Ouvrez le module dans Chrome ou Safari avec WebGL activé.'",
     "'The 3D viewer did not start: '+event.message+'. Open it in Chrome or Safari with WebGL enabled.'"),
    ("a.download='Airplus_assemblage.png'", "a.download='Airplus_assembly.png'"),
    ("// Interface de diagnostic utilisée pour vérifier les retours exacts et les clics réels.",
     "// Diagnostic interface used to check exact returns and real clicks."),
    ("'Erreur du visualiseur : '+err.message+'. Vérifiez que WebGL est disponible dans votre navigateur.'",
     "'Viewer error: '+err.message+'. Check that WebGL is available in your browser.'"),
    ('<html lang="fr">', '<html lang="en">'),
    ("<title>Air+ · Atelier d'assemblage 3D</title>", "<title>Air+ · 3D assembly workshop</title>"),
]

ANOMALIES = {"Durée incompatible avec début/fin": "Duration inconsistent with start/end",
             "Référence manquante": "Missing reference"}
TYPES = {"Texte ou erreur hors référentiel": "Text or error outside the parts master",
         "Incohérence horaire": "Time inconsistency", "Référence manquante": "Missing reference"}


def en_source(s: str) -> str:
    s = s.replace("MES_Extraction_FR.xlsx", "MES_Extraction_UK.xlsx").replace("PLM_DataSet_FR.xlsx", "PLM_DataSet_UK.xlsx")
    return s.replace("/ Sheet1 /", "/ PLM_DATA /").replace("ligne", "row")


def groupe(s: str) -> str:
    for a, b in GROUPES:
        s = s.replace(a, b)
    return s


def main():
    text = HTML.read_text(encoding="utf-8")
    lines = text.split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith("const MODEL="))
    head, tail = lines[i].split(",DATA={", 1)
    assert tail == ""
    model = json.loads(head[len("const MODEL="):])
    j = next(k for k in range(i + 1, len(lines)) if lines[k] == "};")
    data = json.loads("{" + "\n".join(lines[i + 1:j]) + "}")

    for p in model["parts"]:
        p["group"], p["path"], p["name"] = groupe(p["group"]), groupe(p["path"]), groupe(p["name"])
    meta = model["metadata"]
    meta["groups"] = {groupe(k): v for k, v in meta["groups"].items()}
    meta["conversion"] = "STEP tessellation, rigid Z-up to Y-up transform, centring and uniform scale"
    meta["warning"] = "References missing from the CAD names are not inferred from the geometry."
    meta["presentation_orientation"] = ("Rigid presentation rotation set by the centres of Nose, D278, D116 and A623; "
                                        "no relative position changed.")

    mes = load_mes(DATA_DIR).set_index("poste")
    plm = load_plm(DATA_DIR).set_index("code")
    for o in data["operations"]:
        m = mes.loc[o["poste"]]
        o["activite"], o["incident"], o["cause_potentielle"] = m["operation"], m["alea"], m["cause"]
        o["anomalie"] = ANOMALIES.get(o["anomalie"], o["anomalie"])
        o["source"] = en_source(o["source"])
    for p in data["pieces"]:
        r = plm.loc[p["reference"]]
        p["designation"], p["criticite"], p["delai"] = r["designation"], r["criticality"], r["lead_time"]
        p["source"] = en_source(p["source"])
    for x in data["liens"]:
        if x["reference"] in plm.index:
            x["designation"] = plm.loc[x["reference"], "designation"]
        x["criticite"] = CRITICALITY_EN.get(x["criticite"], x["criticite"])
        x["source_mes"], x["source_plm"] = en_source(x["source_mes"]), en_source(x["source_plm"])
    for a in data["anomalies"]:
        a["type"] = TYPES.get(a["type"], a["type"])
        a["decision"] = "Source value kept. Business validation required."
        a["source"] = en_source(a["source"])
        a["detail"] = re.sub(r"Poste (\d+): durée déclarée ([\d.]+) min, intervalle ([\d.]+) min\. Aucune correction automatique\.",
                             r"Station \1: declared duration \2 min, interval \3 min. No automatic correction.", a["detail"])
        a["detail"] = re.sub(r"Poste (\d+), nombre de pièces déclaré: (\d+)", r"Station \1, declared number of parts: \2",
                             a["detail"])
    for e in data["events"]:
        e["activity"] = f"P{e['station']:02d} - {mes.loc[e['station'], 'operation']}"
        e["case_id_origine"] = "Technical assumption, not in the sources"
        e["source"] = en_source(e["source"])
    data.pop("ressources", None)  # prénoms, noms et matricules : inutiles à l'atelier

    lines[i] = "const MODEL=" + json.dumps(model, ensure_ascii=False, separators=(",", ":")) + ",DATA={"
    body = json.dumps(data, ensure_ascii=False, indent=2)
    lines[i + 1:j] = body[1:-1].strip("\n").split("\n")
    out = "\n".join(lines)
    for a, b in INTERFACE:
        if a not in out:
            print("introuvable :", a[:70])
        out = out.replace(a, b)
    HTML.write_text(out, encoding="utf-8")
    print(f"{HTML.name} : {len(text) / 1e6:.2f} Mo -> {len(out) / 1e6:.2f} Mo")


if __name__ == "__main__":
    main()
