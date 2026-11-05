"""Règles du cahier Air+ (version 1.2) : MES01-09, PLM01-06, ERP01-07, CROSS01-06 et score P04.

Tout est calculé sur les données réelles, une ligne par opération MES (CROSS06).
Choix laissés ouverts par le cahier, regroupés ici pour être ajustés facilement :
  - TOLERANCE_HORAIRE_MIN : écart toléré entre (Heure Fin - Heure Début) et Temps Réel (MES05) ;
  - DELAI_RETENU : borne de la fourchette « 15-20 jours » utilisée pour le Q3 (PLM02).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from etl import Dataset

TOLERANCE_HORAIRE_MIN = 1.0
DELAI_RETENU = "max"  # "max" : 15-20 jours compte pour 20 ; "min" ou "milieu" possibles

GRAVITES = ["none", "minor", "major", "critical"]
PRIORITES = ["Low", "Medium", "High"]
POINTS_RETARD = {"none": 0, "minor": 10, "major": 20, "critical": 40}
POINTS_INCIDENT, POINTS_CRITIQUE, POINTS_APPRO = 25, 20, 15

# du vert au rouge selon le degré, comme demandé dans le cahier
COULEURS_GRAVITE = {"none": "#16A34A", "minor": "#EAB308", "major": "#F97316", "critical": "#DC2626",
                    "n/a": "#94A3B8"}
COULEURS_PRIORITE = {"Low": "#16A34A", "Medium": "#EAB308", "High": "#DC2626"}
COULEURS_CRITICITE = {"Low": "#16A34A", "Medium": "#EAB308", "High": "#F97316", "Critical": "#DC2626"}


def gravite(pct: float) -> str:
    """MES03."""
    if pd.isna(pct):
        return "n/a"
    if pct <= 0:
        return "none"
    if pct < 20:
        return "minor"
    if pct < 50:
        return "major"
    return "critical"


def priorite(score: float) -> str:
    if score >= 60:
        return "High"
    if score >= 30:
        return "Medium"
    return "Low"


@dataclass
class Anomalie:
    regle: str
    objet: str
    detail: str
    source: str


@dataclass
class Resultats:
    operations: pd.DataFrame
    pieces: pd.DataFrame
    postes: pd.DataFrame
    anomalies: list[Anomalie] = field(default_factory=list)
    q3_delai: float = float("nan")
    kpi: dict = field(default_factory=dict)
    fin_refs: pd.DataFrame = field(default_factory=pd.DataFrame)
    fin_postes: pd.DataFrame = field(default_factory=pd.DataFrame)
    fin: dict = field(default_factory=dict)


def _delai(plm: pd.DataFrame) -> pd.Series:
    if DELAI_RETENU == "min":
        return plm["lead_min_days"]
    if DELAI_RETENU == "milieu":
        return (plm["lead_min_days"] + plm["lead_max_days"]) / 2
    return plm["lead_max_days"]


def evaluer(ds: Dataset) -> Resultats:
    m = ds.mes.copy()
    plm = ds.plm.copy()
    erp = ds.erp
    anomalies: list[Anomalie] = []

    # PLM01, PLM02, PLM05
    plm["delai_jours"] = _delai(plm)
    valides = plm["delai_jours"].dropna()
    q3 = float(np.percentile(valides, 75)) if len(valides) else float("nan")
    plm["critique"] = plm["criticality"].str.lower() == "critical"
    plm["delai_eleve"] = plm["delai_jours"] > q3
    for r in plm.itertuples():
        manque = [n for n, v in (("criticality", r.criticality), ("supplier", r.supplier),
                                 ("lead time", r.delai_jours)) if pd.isna(v) or str(v).strip() in ("", "nan")]
        if manque:
            anomalies.append(Anomalie("PLM05", r.code, "Missing data: " + ", ".join(manque), r.evidence))
    codes_plm = set(plm["code"])
    critiques = set(plm.loc[plm["critique"], "code"])
    eleves = set(plm.loc[plm["delai_eleve"], "code"])

    # MES01, MES02, MES03, MES04, MES05, MES06
    m["ecart_min"] = m["real_min"] - m["planned_min"]
    m["retard_pct"] = np.where(m["planned_min"] > 0, m["ecart_min"] / m["planned_min"] * 100, np.nan)
    m["gravite"] = m["retard_pct"].map(gravite)
    m["incident"] = m["alea"].fillna("").str.strip().ne("") & m["alea"].ne("nan")
    m["duree_horaire_min"] = (m["end_declared"] - m["start"]).dt.total_seconds() / 60
    m["coherence_horaire"] = (m["duree_horaire_min"] - m["real_min"]).abs() <= TOLERANCE_HORAIRE_MIN
    for r in m[~m["coherence_horaire"]].itertuples():
        anomalies.append(Anomalie(
            "MES05", f"Station {r.poste}",
            f"End time - start time = {r.duree_horaire_min:.1f} min, declared actual time = {r.real_min:.1f} min "
            f"(gap {r.duree_horaire_min - r.real_min:+.1f} min). Flagged, MES not corrected.", r.evidence))
    m["donnees_manquantes"] = m[["start", "end_declared", "planned_min", "real_min"]].isna().any(axis=1)
    for r in m[m["donnees_manquantes"]].itertuples():
        anomalies.append(Anomalie("MES06", f"Station {r.poste}", "Missing date, time or duration: not computable.",
                                  r.evidence))

    # MES07 : chevauchements entre opérations
    iv = m[["poste", "start", "end_declared", "evidence"]].dropna().sort_values("start").to_numpy()
    for i in range(len(iv)):
        for j in range(i + 1, len(iv)):
            if iv[j][1] >= iv[i][2]:
                break
            anomalies.append(Anomalie("MES07", f"Stations {iv[i][0]} and {iv[j][0]}",
                                      "Simultaneous operations: flagged, not assumed to be an anomaly.",
                                      f"{iv[i][3]} · {iv[j][3]}"))

    # PLM03, PLM04, PLM06, CROSS05
    m["refs_distinctes"] = m["refs"].map(lambda r: sorted(set(r)))
    m["refs_inconnues"] = m["refs_distinctes"].map(lambda r: [c for c in r if c not in codes_plm])
    m["pieces_critiques"] = m["refs_distinctes"].map(lambda r: [c for c in r if c in critiques])
    m["pieces_delai_eleve"] = m["refs_distinctes"].map(lambda r: [c for c in r if c in eleves])
    m["nb_pieces_critiques"] = m["pieces_critiques"].map(len)
    for r in m[m["refs_inconnues"].map(len) > 0].itertuples():
        anomalies.append(Anomalie("PLM04 / CROSS05", f"Station {r.poste}",
                                  "References missing from the PLM: " + ", ".join(r.refs_inconnues), r.evidence))

    # ERP01, ERP02, ERP07, CROSS04
    effectif = erp.groupby("poste")["operator_id"].nunique()
    m["effectif_erp"] = m["poste"].map(effectif).fillna(0).astype(int)
    for r in m[m["effectif_erp"] == 0].itertuples():
        anomalies.append(Anomalie("ERP07", f"Station {r.poste}", "No ERP employee ID assigned to the station.", r.evidence))

    # CROSS01-04 : signaux d'investigation, jamais des causes
    retard = m["ecart_min"] > 0
    m["CROSS01"] = retard & (m["nb_pieces_critiques"] > 0)
    m["CROSS02"] = retard & (m["pieces_delai_eleve"].map(len) > 0)
    m["CROSS03"] = retard & m["incident"]
    m["CROSS04"] = retard & (m["effectif_erp"] > 0)

    # P04 : score de priorité d'investigation
    m["pts_retard"] = m["gravite"].map(POINTS_RETARD).fillna(0).astype(int)
    m["pts_incident"] = np.where(m["incident"], POINTS_INCIDENT, 0)
    m["pts_critique"] = np.where(m["nb_pieces_critiques"] > 0, POINTS_CRITIQUE, 0)
    m["pts_appro"] = np.where(m["pieces_delai_eleve"].map(len) > 0, POINTS_APPRO, 0)
    m["score"] = m[["pts_retard", "pts_incident", "pts_critique", "pts_appro"]].sum(axis=1).clip(upper=100)
    limites = []
    for r in m.itertuples():
        lim = []
        if r.gravite == "n/a" or r.donnees_manquantes:
            lim.append("delay not computable")
        if not r.refs_distinctes:
            lim.append("no part reference: PLM criteria cannot be assessed")
        elif r.refs_inconnues:
            lim.append("references missing from the PLM")
        limites.append("; ".join(lim))
    m["limites_score"] = limites
    m["score_provisoire"] = m["limites_score"] != ""
    m["priorite"] = m["score"].map(priorite)

    operations = m[[
        "poste", "operation", "start", "planned_min", "real_min", "ecart_min", "retard_pct", "gravite",
        "incident", "alea", "cause", "refs_distinctes", "pieces_critiques", "pieces_delai_eleve",
        "nb_pieces_critiques", "refs_inconnues", "effectif_erp", "coherence_horaire", "CROSS01", "CROSS02",
        "CROSS03", "CROSS04", "pts_retard", "pts_incident", "pts_critique", "pts_appro", "score", "priorite",
        "score_provisoire", "limites_score", "evidence",
    ]].copy()
    assert operations["poste"].is_unique  # CROSS06 : une ligne par opération

    usage = pd.Series([c for refs in operations["refs_distinctes"] for c in refs]).value_counts()
    pieces = plm.assign(operations_mes=plm["code"].map(usage).fillna(0).astype(int))[[
        "code", "designation", "criticality", "critique", "lead_time", "delai_jours", "delai_eleve", "supplier",
        "unit_cost_eur", "operations_mes", "evidence"]]

    postes = _postes(ds, operations)
    fin_refs, fin_postes, fin, fin_anom = _finance(ds)
    anomalies += fin_anom
    inconnues = sorted({c for refs in operations["refs_inconnues"] for c in refs})
    analysables = operations[operations["retard_pct"].notna()]
    kpi = {
        "operations": len(operations),
        "taux_retard": float((analysables["ecart_min"] > 0).mean() * 100) if len(analysables) else float("nan"),
        "retards_importants": int((operations["gravite"] == "major").sum()),
        "retards_critiques": int((operations["gravite"] == "critical").sum()),
        "retard_cumule_min": float(operations["ecart_min"].clip(lower=0).sum()),
        "incidents": int(operations["incident"].sum()),
        "priorites_elevees": int((operations["score"] >= 60).sum()),
        "scores_provisoires": int(operations["score_provisoire"].sum()),
        "pieces_critiques": int(pieces["critique"].sum()),
        "pieces_delai_eleve": int(pieces["delai_eleve"].sum()),
        "operations_piece_critique": int((operations["nb_pieces_critiques"] > 0).sum()),
        "references_inconnues": len(inconnues),
        "ressources_erp": int(ds.erp["operator_id"].nunique()),
        "postes_sans_erp": int((operations["effectif_erp"] == 0).sum()),
    }
    kpi["cout_total_pieces"] = fin["cout_total"]
    return Resultats(operations, pieces, postes, anomalies, q3, kpi, fin_refs, fin_postes, fin)


def _valide(x) -> bool:
    try:
        return pd.notna(x) and float(x) >= 0
    except (TypeError, ValueError):
        return False


def _finance(ds: Dataset):
    """FIN01-05 : coûts de référence PLM et ERP. Ce ne sont pas des pertes liées aux retards."""
    anomalies = []
    p = ds.plm[["code", "designation", "qty_bom", "unit_cost_eur", "criticality", "supplier", "evidence"]].copy()
    p["valide"] = p["qty_bom"].map(_valide) & p["unit_cost_eur"].map(_valide)
    for r in p[~p["valide"]].itertuples():
        anomalies.append(Anomalie("FIN", r.code, "Quantity or cost missing, non-numeric or negative: excluded.",
                                  r.evidence))
    v = p[p["valide"]].copy()
    v["cout_reference"] = v["qty_bom"].astype(float) * v["unit_cost_eur"].astype(float)  # FIN02
    total = float(v["cout_reference"].sum())  # FIN01
    v["part_pct"] = v["cout_reference"] / total * 100 if total > 0 else np.nan  # FIN03
    v = v.sort_values("cout_reference", ascending=False)
    v["part_cumulee_pct"] = v["part_pct"].cumsum()

    e = ds.erp[["poste", "operator_id", "hourly_cost_eur", "evidence"]].drop_duplicates("operator_id")
    e = e.assign(valide=e["hourly_cost_eur"].map(_valide))
    for r in e[~e["valide"]].itertuples():
        anomalies.append(Anomalie("FIN", f"Station {r.poste}", "Invalid hourly cost: resource excluded.", r.evidence))
    ev = e[e["valide"]]
    g = ev.groupby("poste")["hourly_cost_eur"]
    fp = pd.DataFrame({
        "cout_horaire_theorique": g.sum(),  # FIN04
        "ressources_valides": g.count(),
        "cout_horaire_moyen": g.mean(),  # FIN05
    }).reset_index()
    fp = ds.mes[["poste", "operation"]].merge(fp, on="poste", how="left")
    fp["evidence"] = "ERP:P" + fp["poste"].astype(str)
    fin = {
        "cout_total": total,
        "references_valorisees": int(len(v)),
        "donnees_invalides": int((~p["valide"]).sum() + (~e["valide"]).sum()),
        "top_reference": v["code"].iat[0] if len(v) else None,
        "top_part_pct": float(v["part_pct"].iat[0]) if len(v) else float("nan"),
        "top2_part_pct": float(v["part_pct"].head(2).sum()) if len(v) else float("nan"),
        "refs_80pct": int((v["part_cumulee_pct"] < 80).sum() + 1) if len(v) else 0,
        "fin04_min": float(fp["cout_horaire_theorique"].min()),
        "fin04_max": float(fp["cout_horaire_theorique"].max()),
        "fin04_moyen": float(fp["cout_horaire_theorique"].mean()),
        "fin05_moyen": float(ev["hourly_cost_eur"].mean()),
    }
    return v, fp, fin, anomalies


def depassement_par_poste(res: Resultats) -> pd.DataFrame:
    """Σ max(0, Temps Réel - Temps Prévu) par poste."""
    o = res.operations
    return (o.assign(dep=o["ecart_min"].clip(lower=0)).groupby(["poste", "operation"], as_index=False)["dep"].sum()
            .sort_values("dep", ascending=False))


def _postes(ds: Dataset, ops: pd.DataFrame) -> pd.DataFrame:
    """ERP02-06 par poste : effectifs et synthèses, jamais de ligne individuelle."""
    rows = []
    for poste, g in ds.erp.groupby("poste"):
        comp = sorted({c.strip() for s in g["skills"] for c in str(s).split(",") if c.strip()})
        hab = sorted({h.strip() for s in g["certifications"] for h in str(s).split(",") if h.strip()})
        rows.append({
            "poste": int(poste),
            "effectif": int(g["operator_id"].nunique()),
            "qualifications": "; ".join(f"{k} ({v})" for k, v in g["qualification"].value_counts().items()),
            "niveaux": "; ".join(f"{k} ({v})" for k, v in g["level"].value_counts().items()),
            "competences": "; ".join(comp),
            "habilitations": "; ".join(hab),
            "evidence": f"ERP:P{int(poste)}",
        })
    out = pd.DataFrame(rows)
    return ops[["poste", "operation", "gravite", "priorite"]].merge(out, on="poste", how="left")


def resume_scores(res: Resultats) -> pd.DataFrame:
    return (res.operations.groupby("priorite").agg(operations=("poste", "count"),
                                                   score_moyen=("score", "mean"))
            .reindex(PRIORITES).fillna(0).reset_index())
