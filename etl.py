"""Chargement et alignement des sources Air+ : MES, PLM, ERP, notice, CAO.

Chaque ligne garde un identifiant de preuve (MES:L12, PLM:L5, ERP:L40, NOTICE:p25...) qui
correspond au numéro de ligne Excel ou à la page du PDF. C'est ce qui permet à l'assistant IA
de citer ses sources.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"

MES_FILE = "MES_Extraction_FR.xlsx"
PLM_FILE = "PLM_DataSet_FR.xlsx"
ERP_FILE = "ERP_Equipes Airplus_FR.xlsx"
NOTICE_STEPS_FILE = "ref_process_notice.csv"
NOTICE_INVENTORY_FILE = "inventaire_notice.csv"
CAD_FILE = "airplus_787.glb"

SHIFT_START = dt.time(8, 0)
SHIFT_END = dt.time(17, 0)

PART_CODE = re.compile(r"\b[A-D]\d{3}\b")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def to_minutes(value) -> float:
    """Les durées Excel arrivent en datetime.time (00:32:45) ou en texte."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return float("nan")
    if isinstance(value, dt.time):
        return value.hour * 60 + value.minute + value.second / 60
    if isinstance(value, dt.timedelta):
        return value.total_seconds() / 60
    h, m, *s = str(value).split(":")
    return int(h) * 60 + int(m) + (float(s[0]) if s else 0) / 60


# Familles d'aléas. L'ordre compte : la première règle qui matche gagne.
ALEA_FAMILIES = [
    ("MAR", "Marquage et finition", ["marquage", "impression", "etiquette", "sticker", "adherence"]),
    ("ELE", "Électrique", ["electrique", "connectique"]),
    ("ENV", "Environnement atelier", ["ventilation", "temperature", "contamination", "pression", "thermique"]),
    ("SIS", "Systèmes d'information et automatismes",
     ["automatis", "logiciel", "robot", "reseau", "synchronisation", "communication", "tracabilite",
      "referencement", "protocole"]),
    ("QUA", "Contrôle qualité et métrologie", ["controle", "calibration", "etalonnage", "derive"]),
    ("OUT", "Outillage et usure", ["outillage", "usure", "gabarit"]),
    ("LOG", "Logistique et manutention", ["manutention", "transport"]),
]
FAMILY_LABELS = {code: label for code, label, _ in ALEA_FAMILIES} | {"MEC": "Machines et équipements"}


def alea_family(text: str) -> str:
    t = strip_accents(str(text).lower())
    for code, _, keywords in ALEA_FAMILIES:
        if any(k in t for k in keywords):
            return code
    return "MEC"


def load_mes(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    raw = pd.read_excel(data_dir / MES_FILE)
    rows = []
    for i, r in raw.iterrows():
        line = i + 2  # ligne 1 = en-tête Excel
        day = pd.Timestamp(r["Date"]).normalize()
        start = day + pd.Timedelta(minutes=to_minutes(r["Heure Début"]))
        end_declared = day + pd.Timedelta(minutes=to_minutes(r["Heure Fin"]))
        refs_raw = r["Référence"]
        refs = [] if pd.isna(refs_raw) else [x.strip() for x in str(refs_raw).split(";") if x.strip()]
        planned = to_minutes(r["Temps Prévu"])
        real = to_minutes(r["Temps Réel"])
        rows.append({
            "poste": int(r["Poste"]),
            "operation": str(r["Nom"]).strip(),
            "n_pieces": int(r["Nombre pièces"]),
            "refs": refs,
            "planned_min": planned,
            "real_min": real,
            "start": start,
            "end_declared": end_declared,
            "end_from_duration": start + pd.Timedelta(minutes=real),
            "alea": str(r["Aléas Industriels"]).strip(),
            "cause": str(r["Cause Potentielle"]).strip(),
            "mes_line": line,
            "evidence": f"MES:L{line}",
        })
    mes = pd.DataFrame(rows)
    mes["overrun_min"] = mes["real_min"] - mes["planned_min"]
    mes["overrun_pct"] = mes["overrun_min"] / mes["planned_min"] * 100
    mes["family"] = mes["alea"].map(alea_family)
    mes["activity"] = mes.apply(lambda r: f"P{r.poste:02d} {r.operation}", axis=1)
    mes["truncated"] = (mes["end_from_duration"] - mes["end_declared"]).dt.total_seconds().abs() > 30
    return mes


def _parse_delay(text) -> tuple[float, float]:
    nums = [float(n) for n in re.findall(r"\d+", str(text))]
    if not nums:
        return float("nan"), float("nan")
    return nums[0], nums[-1]


def load_plm(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    raw = pd.read_excel(data_dir / PLM_FILE, sheet_name="Sheet1")
    rows = []
    for i, r in raw.iterrows():
        code = str(r["Code / Référence"]).strip()
        if not PART_CODE.fullmatch(code):
            continue
        dmin, dmax = _parse_delay(r["Délai Approvisionnement"])
        rows.append({
            "code": code,
            "designation": str(r["Désignation"]).strip(),
            "qty_bom": int(r["Quantité"]),
            "supplier": str(r["Fournisseur"]).strip(),
            "lead_time": str(r["Délai Approvisionnement"]).strip(),
            "lead_min_days": dmin,
            "lead_max_days": dmax,
            "criticality": str(r["Criticité"]).strip(),
            "mass_kg": float(r["Masse (kg)"]),
            "unit_cost_eur": float(r["Coût achat pièce (€)"]),
            "cad_hours": float(r["Temps CAO (h)"]),
            "plm_line": i + 2,
            "evidence": f"PLM:{code}",
        })
    return pd.DataFrame(rows)


def plm_second_sheet_noise(data_dir: Path = DATA_DIR) -> list[dict]:
    """Lignes de l'onglet Feuil1 qui ne sont pas des références pièce (texte parasite)."""
    raw = pd.read_excel(data_dir / PLM_FILE, sheet_name="Feuil1", header=None)
    noise = []
    for i, val in raw[0].items():
        if pd.isna(val) or i == 0:
            continue
        s = str(val).strip()
        if not PART_CODE.fullmatch(s):
            noise.append({"line": i + 1, "text": s})
    return noise


# Données ERP : on ne garde jamais les identifiants directs hors de ce module.
ERP_DIRECT_IDENTIFIERS = ["Prénom", "Nom", "Commentaire de Carrière"]
ERP_QUASI_IDENTIFIERS = ["poste", "age_band", "qualification", "level"]


def pseudonym(matricule: str, salt: str) -> str:
    digest = hmac.new(salt.encode(), matricule.encode(), hashlib.sha256).hexdigest()
    return f"OP-{digest[:6].upper()}"


def load_erp_pseudonymized(data_dir: Path = DATA_DIR, salt: str | None = None) -> pd.DataFrame:
    """Pseudonymisation à la lecture : HMAC du matricule, suppression nom/prénom/commentaire,
    âge passé en tranche de 10 ans. La clé (AIRPLUS_SALT) reste hors du dépôt."""
    salt = salt or os.environ.get("AIRPLUS_SALT", "change-me-before-demo")
    raw = pd.read_excel(data_dir / ERP_FILE)
    out = pd.DataFrame({
        "operator_id": raw["Matricule"].astype(str).map(lambda m: pseudonym(m, salt)),
        "poste": raw["Poste de montage"].astype(str).str.extract(r"(\d+)")[0].astype(int),
        "age_band": (raw["Âge"] // 10 * 10).astype(int).astype(str) + "-" + (raw["Âge"] // 10 * 10 + 9).astype(int).astype(str),
        "qualification": raw["Qualification"].astype(str),
        "level": raw["Niveau d'expérience"].astype(str),
        "skills": raw["Compétences"].astype(str),
        "certifications": raw["Habilitations"].astype(str),
        "hourly_cost_eur": raw["Coût horaire (€)"].astype(float),
        "rotation": raw["Rotation"].astype(str),
    })
    out["erp_line"] = range(2, len(out) + 2)
    out["evidence"] = "ERP:L" + out["erp_line"].astype(str)
    return out


def k_anonymity(df: pd.DataFrame, quasi: list[str]) -> dict:
    sizes = df.groupby(quasi).size()
    return {
        "quasi_identifiers": quasi,
        "k": int(sizes.min()),
        "groups": int(len(sizes)),
        "unique_rows": int((sizes == 1).sum()),
        "unique_share": float((sizes == 1).sum() / len(df)),
    }


def load_notice(data_dir: Path = DATA_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    steps = pd.read_csv(data_dir / NOTICE_STEPS_FILE)
    steps["evidence"] = "NOTICE:p" + steps["page_notice"].astype(str) + "-e" + steps["etape"].astype(str)
    inv = pd.read_csv(data_dir / NOTICE_INVENTORY_FILE, keep_default_na=False)
    inv["evidence"] = "NOTICE:p43"
    return steps, inv


def load_cad_counts(data_dir: Path = DATA_DIR) -> pd.Series:
    """Nombre d'occurrences de chaque code pièce dans l'arbre du modèle 3D (.glb)."""
    path = data_dir / CAD_FILE
    if not path.exists():
        return pd.Series(dtype=int)
    blob = path.read_bytes()
    json_len = struct.unpack("<I", blob[12:16])[0]
    gltf = json.loads(blob[20:20 + json_len])
    codes = [m for n in gltf.get("nodes", []) for m in PART_CODE.findall(n.get("name", ""))]
    return pd.Series(codes, dtype=str).value_counts()


def consumption(mes: pd.DataFrame) -> pd.Series:
    return pd.Series([r for refs in mes["refs"] for r in refs], dtype=str).value_counts()


def bom_vs_sources(plm: pd.DataFrame, mes: pd.DataFrame, notice_inv: pd.DataFrame,
                   cad: pd.Series) -> pd.DataFrame:
    used = consumption(mes)
    notice_q = notice_inv[notice_inv["code"] != ""].set_index("code")["quantite"]
    codes = sorted(set(plm["code"]) | set(used.index) | set(notice_q.index))
    plm_q = plm.set_index("code")["qty_bom"]
    df = pd.DataFrame({
        "code": codes,
        "notice": [notice_q.get(c) for c in codes],
        "plm": [plm_q.get(c) for c in codes],
        "mes": [int(used.get(c, 0)) for c in codes],
        "cad": [int(cad.get(c, 0)) if len(cad) else None for c in codes],
    })
    df = df.merge(plm[["code", "designation", "criticality", "supplier"]], on="code", how="left")
    df["mes_vs_plm"] = df["mes"] - df["plm"].fillna(0)
    return df


@dataclass
class Finding:
    id: str
    title: str
    detail: str
    severity: str
    evidence: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)  # sujets sur lesquels l'IA doit refuser de conclure


def quality_checks(mes, plm, notice_steps, notice_inv, bom, plm_noise) -> list[Finding]:
    findings: list[Finding] = []

    trunc = mes[mes["truncated"]]
    lost = (trunc["end_from_duration"] - trunc["end_declared"]).dt.total_seconds().sum() / 60
    findings.append(Finding(
        "DQ-01", "Opérations clôturées d'office à 17:00",
        f"Postes {', '.join(map(str, trunc['poste']))} : l'heure de fin vaut 17:00 alors que début + temps réel "
        f"dépasse. {lost:.0f} minutes de travail déclaré ne sont placées nulle part dans le temps.",
        "haute", trunc["evidence"].tolist(), ["heure de fin", "temps de fin de poste"]))

    no_ref = mes[mes["refs"].map(len) == 0]
    step51 = notice_steps[notice_steps["liste_pieces"] == "non"]
    findings.append(Finding(
        "DQ-02", "Opération sans référence pièce",
        f"Poste {', '.join(map(str, no_ref['poste']))} sans pièce dans le MES. La notice (étape "
        f"{', '.join(map(str, step51['etape']))}) n'a pas non plus de liste de pièces : c'est une étape de "
        "câblage, pas une erreur de saisie.",
        "info", no_ref["evidence"].tolist() + step51["evidence"].tolist()))

    gaps = bom[(bom["plm"].notna()) & (bom["mes_vs_plm"] != 0) & (bom["mes"] > 0)]
    findings.append(Finding(
        "DQ-03", "Consommation MES différente de la nomenclature",
        f"{len(gaps)} références : " + "; ".join(
            f"{r.code} {int(r.plm)} prévu / {r.mes} consommé" for r in gaps.itertuples()) +
        ". Le MES liste une référence à chaque usage : on ne sait pas s'il s'agit d'unités consommées "
        "ou de passages.",
        "haute", [f"PLM:{c}" for c in gaps["code"]], ["consommation réelle de pièces"]))

    never = bom[(bom["plm"].notna()) & (bom["mes"] == 0)]
    findings.append(Finding(
        "DQ-04", "Références de la nomenclature jamais consommées",
        "Jamais vues dans le MES : " + ", ".join(f"{r.code} ({r.designation})" for r in never.itertuples()) +
        ". D145 (queue arrière gauche) est consommée deux fois et D144 (queue arrière droite) jamais : "
        "erreur de référence probable sur la queue.",
        "haute", [f"PLM:{c}" for c in never["code"]]))

    cad_issue = bom[(bom["code"].isin(["D144", "D145"]))]
    if cad_issue["cad"].notna().any():
        findings.append(Finding(
            "DQ-05", "La queue arrière diffère selon les systèmes",
            "Notice et PLM : D144 x1 et D145 x1. MES : D145 x" +
            str(int(cad_issue.set_index('code').loc['D145', 'mes'])) + ", D144 x0. Maquette 3D : D144 x" +
            str(int(cad_issue.set_index('code').loc['D144', 'cad'])) + ", D145 x" +
            str(int(cad_issue.set_index('code').loc['D145', 'cad'])) + ". Trois systèmes, trois versions.",
            "haute", ["PLM:D144", "PLM:D145", "NOTICE:p43", "CAD:D144"]))

    if "C994" in set(notice_inv["code"]) and "C984" in set(plm["code"]):
        findings.append(Finding(
            "DQ-06", "Code du tournevis différent entre notice et PLM",
            "La notice référence le tournevis C994, le PLM C984. Outils (C984, C993) et planche "
            "d'autocollants (C995) sont gérés comme des pièces dans la nomenclature.",
            "moyenne", ["NOTICE:p43", "PLM:C984", "PLM:C993"]))

    if plm_noise:
        findings.append(Finding(
            "DQ-07", "Texte parasite dans le PLM",
            f"L'onglet Feuil1 contient {len(plm_noise)} lignes qui ne sont pas des pièces, dont « "
            f"{plm_noise[-1]['text'][:80]} » : reste d'une génération par IA non nettoyée.",
            "moyenne", [f"PLM-F1:L{n['line']}" for n in plm_noise[:3]]))

    findings.append(Finding(
        "DQ-08", "Données manifestement synthétiques",
        "Les ailes gauche et droite ont des durées identiques à la seconde, chaque opération porte un aléa "
        "différent et un seul. Les causes déclarées ne sont pas vérifiées : on les traite comme des pistes.",
        "info", ["MES:L21", "MES:L37"], ["cause racine"]))

    findings.append(Finding(
        "DQ-09", "Informations absentes des trois systèmes",
        "Aucune révision PLM, aucun lot ERP, aucun code défaut, aucune ressource par opération, un seul "
        "avion tracé. Tout ce qui en dépend dans l'application est simulé et étiqueté comme tel.",
        "haute", ["MES:L1", "PLM:L1", "ERP:L1"], ["lot", "révision", "défaut", "opérateur"]))
    return findings


@dataclass
class Dataset:
    mes: pd.DataFrame
    plm: pd.DataFrame
    erp: pd.DataFrame
    notice_steps: pd.DataFrame
    notice_inv: pd.DataFrame
    cad: pd.Series
    bom: pd.DataFrame
    findings: list[Finding]
    kanon_full: dict
    kanon_released: dict


def load_all(data_dir: Path = DATA_DIR) -> Dataset:
    mes = load_mes(data_dir)
    plm = load_plm(data_dir)
    erp = load_erp_pseudonymized(data_dir)
    steps, inv = load_notice(data_dir)
    cad = load_cad_counts(data_dir)
    bom = bom_vs_sources(plm, mes, inv, cad)
    findings = quality_checks(mes, plm, steps, inv, bom, plm_second_sheet_noise(data_dir))
    return Dataset(
        mes=mes, plm=plm, erp=erp, notice_steps=steps, notice_inv=inv, cad=cad, bom=bom,
        findings=findings,
        kanon_full=k_anonymity(erp, ERP_QUASI_IDENTIFIERS),
        kanon_released=k_anonymity(erp, ["poste"]),
    )
