"""Chargement et alignement des sources Air+ : MES, PLM, ERP, notice, CAO.

Chaque ligne garde un identifiant de preuve (MES:L12, PLM:L5, ERP:L40, NOTICE:p25...) qui
correspond au numéro de ligne Excel ou à la page du PDF. C'est ce qui permet à l'assistant IA
de citer ses sources.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"

# Extractions en anglais (UK) si elles sont présentes dans data/, sinon les françaises (FR)
MES_FILES = ["MES_Extraction_UK.xlsx", "MES_Extraction_FR.xlsx"]
PLM_FILES = ["PLM_DataSet_UK.xlsx", "PLM_DataSet_FR.xlsx"]
ERP_FILES = ["ERP_Equipes_Airplus_UK.xlsx", "ERP_Equipes Airplus_UK.xlsx", "ERP_Equipes Airplus_FR.xlsx"]
NOTICE_STEPS_FILE = "ref_process_notice.csv"
NOTICE_INVENTORY_FILE = "inventaire_notice.csv"
CAD_FILE = "airplus_787.glb"

SHIFT_START = dt.time(8, 0)
SHIFT_END = dt.time(17, 0)

PART_CODE = re.compile(r"\b[A-D]\d{3}\b")


def _source(data_dir: Path, names: list[str]) -> Path:
    for n in names:
        if (data_dir / n).exists():
            return data_dir / n
    raise FileNotFoundError(f"Aucun de ces fichiers dans {data_dir} : {', '.join(names)}")


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    """Colonne d'après son nom FR ou UK (les en-têtes UK ont parfois des espaces en trop)."""
    cols = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.strip().lower() in cols:
            return df[cols[n.strip().lower()]]
    raise KeyError(f"Colonne introuvable : {' / '.join(names)}")


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
    ("MAR", "Marking and finishing", ["marquage", "impression", "etiquette", "sticker", "adherence",
                                      "marking", "print", "label", "adhesion"]),
    ("ELE", "Electrical", ["electrique", "connectique", "electric"]),
    ("ENV", "Shop-floor environment", ["ventilation", "temperature", "contamination", "pression", "thermique",
                                       "pressure", "thermal"]),
    ("SIS", "IT systems and automation",
     ["automatis", "logiciel", "robot", "reseau", "synchronisation", "communication", "tracabilite",
      "referencement", "protocole", "automat", "software", "network", "synchroni", "traceability", "reference",
      "protocol"]),
    ("QUA", "Quality control and metrology", ["controle", "calibration", "etalonnage", "derive", "control",
                                              "drift"]),
    ("OUT", "Tooling and wear", ["outillage", "usure", "gabarit", "tool", "wear", "gauge"]),
    ("LOG", "Logistics and handling", ["manutention", "transport", "handling"]),
]
FAMILY_LABELS = {code: label for code, label, _ in ALEA_FAMILIES} | {"MEC": "Machines and equipment"}


def alea_family(text: str) -> str:
    t = strip_accents(str(text).lower())
    for code, _, keywords in ALEA_FAMILIES:
        if any(k in t for k in keywords):
            return code
    return "MEC"


def load_mes(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    raw = pd.read_excel(_source(data_dir, MES_FILES))
    c = {k: _col(raw, *n) for k, n in {
        "poste": ("Poste", "Workstation"), "nom": ("Nom", "Name"), "n": ("Nombre pièces", "Quantity"),
        "ref": ("Référence", "Part Reference"), "prevu": ("Temps Prévu", "Planned Time"), "date": ("Date",),
        "debut": ("Heure Début", "Start Time"), "fin": ("Heure Fin", "End Time"), "reel": ("Temps Réel", "Actual Time"),
        "alea": ("Aléas Industriels", "Industrial Issues"), "cause": ("Cause Potentielle", "Potential Cause"),
    }.items()}
    rows = []
    for i in range(len(raw)):
        r = {k: v.iat[i] for k, v in c.items()}
        line = i + 2  # ligne 1 = en-tête Excel
        day = pd.Timestamp(r["date"]).normalize()
        start = day + pd.Timedelta(minutes=to_minutes(r["debut"]))
        end_declared = day + pd.Timedelta(minutes=to_minutes(r["fin"]))
        refs_raw = r["ref"]
        refs = [] if pd.isna(refs_raw) else [x.strip() for x in str(refs_raw).split(";") if x.strip()]
        planned = to_minutes(r["prevu"])
        real = to_minutes(r["reel"])
        rows.append({
            "poste": int(r["poste"]),
            "operation": str(r["nom"]).strip(),
            "n_pieces": int(r["n"]),
            "refs": refs,
            "planned_min": planned,
            "real_min": real,
            "start": start,
            "end_declared": end_declared,
            "end_from_duration": start + pd.Timedelta(minutes=real),
            "alea": str(r["alea"]).strip(),
            "cause": str(r["cause"]).strip(),
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


# valeurs catégorielles des fichiers sources, affichées en anglais
CRITICALITY_EN = {"Critique": "Critical", "Haute": "High", "Moyenne": "Medium", "Basse": "Low"}
LEVEL_EN = {"Débutant": "Beginner", "Confirmé": "Experienced", "Expert": "Expert"}  # libellés du fichier UK


def _parse_delay(text) -> tuple[float, float]:
    nums = [float(n) for n in re.findall(r"\d+", str(text))]
    if not nums:
        return float("nan"), float("nan")
    return nums[0], nums[-1]


def load_plm(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    raw = pd.read_excel(_source(data_dir, PLM_FILES), sheet_name=0)  # Sheet1 (FR) ou PLM_DATA (UK)
    c = {k: _col(raw, *n) for k, n in {
        "code": ("Code / Référence",), "des": ("Désignation", "Part Description"),
        "qty": ("Quantité", "Quantity Required"), "sup": ("Fournisseur", "Supplier Name"),
        "lead": ("Délai Approvisionnement", "Procurement Lead Time (days", "Procurement Lead Time (days)"),
        "crit": ("Criticité", "Criticality Level"), "mass": ("Masse (kg)", "Mass (kg)"),
        "cost": ("Coût achat pièce (€)", "Purchase Cost per Unit (€)"), "cad": ("Temps CAO (h)", "CAD Design Hours"),
    }.items()}
    rows = []
    for i in range(len(raw)):
        r = {k: v.iat[i] for k, v in c.items()}
        code = str(r["code"]).strip()
        if not PART_CODE.fullmatch(code):
            continue
        dmin, dmax = _parse_delay(r["lead"])
        crit = str(r["crit"]).strip()
        rows.append({
            "code": code,
            "designation": str(r["des"]).strip(),
            "qty_bom": int(r["qty"]),
            "supplier": str(r["sup"]).strip(),
            "lead_time": str(r["lead"]).strip().replace("jours", "days").replace("jour", "day"),
            "lead_min_days": dmin,
            "lead_max_days": dmax,
            "criticality": CRITICALITY_EN.get(crit, crit),
            "mass_kg": float(r["mass"]),
            "unit_cost_eur": float(r["cost"]),
            "cad_hours": float(r["cad"]),
            "plm_line": i + 2,
            "evidence": f"PLM:{code}",
        })
    return pd.DataFrame(rows)


def plm_second_sheet_noise(data_dir: Path = DATA_DIR) -> list[dict]:
    """Lignes de l'onglet Feuil1 qui ne sont pas des références pièce (texte parasite)."""
    raw = pd.read_excel(_source(data_dir, PLM_FILES), sheet_name="Feuil1", header=None)
    noise = []
    for i, val in raw[0].items():
        if pd.isna(val) or i == 0:
            continue
        s = str(val).strip()
        if not PART_CODE.fullmatch(s):
            noise.append({"line": i + 1, "text": s})
    return noise


# Données ERP : le matricule est conservé, les noms, prénoms et commentaires ne sont jamais chargés.
ERP_DIRECT_IDENTIFIERS = ["Prénom", "Nom", "Commentaire de Carrière", "First Name", "Last Name", "Career Remarks"]
ERP_QUASI_IDENTIFIERS = ["poste", "age_band", "qualification", "level"]


def load_erp(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Matricule ERP tel quel ; nom, prénom et commentaire de carrière écartés, âge en tranche de 10 ans."""
    raw = pd.read_excel(_source(data_dir, ERP_FILES))
    age = _col(raw, "Âge", "Age")
    out = pd.DataFrame({
        "operator_id": _col(raw, "Matricule", "Employee ID").astype(str).str.strip(),
        "poste": _col(raw, "Poste de montage", "Assembly Station").astype(str).str.extract(r"(\d+)")[0].astype(int),
        "age_band": (age // 10 * 10).astype(int).astype(str) + "-" + (age // 10 * 10 + 9).astype(int).astype(str),
        "qualification": _col(raw, "Qualification").astype(str),
        "level": _col(raw, "Niveau d'expérience", "Experience Level").astype(str).map(lambda v: LEVEL_EN.get(v, v)),
        "skills": _col(raw, "Compétences", "Skills and Competencies").astype(str),
        "certifications": _col(raw, "Habilitations", "Certifications").astype(str),
        "hourly_cost_eur": _col(raw, "Coût horaire (€)", "Hourly Rate (€)").astype(float),
        "rotation": _col(raw, "Rotation", "Shift Rotation").astype(str),
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
        "DQ-01", "Operations force-closed at 17:00",
        f"Stations {', '.join(map(str, trunc['poste']))}: the end time is 17:00 although start + actual time goes "
        f"beyond it. {lost:.0f} minutes of declared work are not placed anywhere in time.",
        "haute", trunc["evidence"].tolist(), ["heure de fin", "temps de fin de poste"]))

    no_ref = mes[mes["refs"].map(len) == 0]
    step51 = notice_steps[notice_steps["liste_pieces"] == "non"]
    findings.append(Finding(
        "DQ-02", "Operation without a part reference",
        f"Station {', '.join(map(str, no_ref['poste']))} has no part in the MES. The manual (step "
        f"{', '.join(map(str, step51['etape']))}) has no parts list either: it is a wiring step, not a data "
        "entry error.",
        "info", no_ref["evidence"].tolist() + step51["evidence"].tolist()))

    gaps = bom[(bom["plm"].notna()) & (bom["mes_vs_plm"] != 0) & (bom["mes"] > 0)]
    findings.append(Finding(
        "DQ-03", "MES consumption differs from the bill of materials",
        f"{len(gaps)} references: " + "; ".join(
            f"{r.code} {int(r.plm)} planned / {r.mes} consumed" for r in gaps.itertuples()) +
        ". The MES lists a reference at each use: it is unclear whether these are consumed units or "
        "passes.",
        "haute", [f"PLM:{c}" for c in gaps["code"]], ["consommation réelle de pièces"]))

    never = bom[(bom["plm"].notna()) & (bom["mes"] == 0)]
    findings.append(Finding(
        "DQ-04", "Bill of materials references never consumed",
        "Never seen in the MES: " + ", ".join(f"{r.code} ({r.designation})" for r in never.itertuples()) +
        ". D145 (left rear tail) is consumed twice and D144 (right rear tail) never: likely reference error "
        "on the tail.",
        "haute", [f"PLM:{c}" for c in never["code"]]))

    cad_issue = bom[(bom["code"].isin(["D144", "D145"]))]
    if cad_issue["cad"].notna().any():
        findings.append(Finding(
            "DQ-05", "The rear tail differs between systems",
            "Manual and PLM: D144 x1 and D145 x1. MES: D145 x" +
            str(int(cad_issue.set_index('code').loc['D145', 'mes'])) + ", D144 x0. 3D model: D144 x" +
            str(int(cad_issue.set_index('code').loc['D144', 'cad'])) + ", D145 x" +
            str(int(cad_issue.set_index('code').loc['D145', 'cad'])) + ". Three systems, three versions.",
            "haute", ["PLM:D144", "PLM:D145", "NOTICE:p43", "CAD:D144"]))

    if "C994" in set(notice_inv["code"]) and "C984" in set(plm["code"]):
        findings.append(Finding(
            "DQ-06", "Screwdriver code differs between manual and PLM",
            "The manual references screwdriver C994, the PLM C984. Tools (C984, C993) and the sticker "
            "sheet (C995) are managed as parts in the bill of materials.",
            "moyenne", ["NOTICE:p43", "PLM:C984", "PLM:C993"]))

    if plm_noise:
        findings.append(Finding(
            "DQ-07", "Stray text in the PLM",
            f"Sheet Feuil1 contains {len(plm_noise)} rows that are not parts, including \""
            f"{plm_noise[-1]['text'][:80]}\": leftover from an uncleaned AI generation.",
            "moyenne", [f"PLM-F1:L{n['line']}" for n in plm_noise[:3]]))

    findings.append(Finding(
        "DQ-08", "Clearly synthetic data",
        "Left and right wings have durations identical to the second, and each operation carries exactly one, "
        "different, incident. Declared causes are not verified: they are treated as leads.",
        "info", ["MES:L21", "MES:L37"], ["cause racine"]))

    findings.append(Finding(
        "DQ-09", "Information missing from all three systems",
        "No PLM revision, no ERP batch, no defect code, no resource per operation, a single aircraft traced. "
        "Anything in the application that depends on them is simulated and labelled as such.",
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
    erp = load_erp(data_dir)
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
