"""Journal d'événements : l'avion réel (MSN-001) et une flotte simulée calibrée dessus.

Le MES ne trace qu'un avion, ce qui ne permet ni variantes, ni encours, ni goulot au sens du
flux. On simule donc N avions qui passent par les mêmes 56 postes, avec :
  - les temps prévus du MES et le ratio réel/prévu observé poste par poste sur MSN-001 ;
  - un lancement au takt prévu (par défaut le temps prévu du poste le plus long) ;
  - des reprises tirées selon la famille d'aléa du poste (hypothèse calibrée sur les 25 % du brief) ;
  - deux écarts de séquence plausibles : lumières posées avant les faisceaux, attente pièce
    sur les références critiques à long délai.
Tout ce qui sort d'ici porte source = "simulé". Les révisions PLM et lots ERP sont synthétiques.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from etl import Dataset

DAY_MIN = 9 * 60  # 08:00 - 17:00, sept jours sur sept comme dans le MES (1er-4 septembre 2023)

# Probabilité de reprise par famille d'aléa du poste. Hypothèse, pas une mesure.
REWORK_BY_FAMILY = {
    "QUA": 0.40, "ENV": 0.28, "ELE": 0.28, "MEC": 0.23, "OUT": 0.23, "SIS": 0.18, "MAR": 0.18, "LOG": 0.14,
}

# Modifications d'ingénierie fictives, pour montrer le fil numérique avec des révisions.
ECOS = [
    {"eco": "ECO-001", "code": "D142", "rev": "B", "from_case": 12,
     "motif": "Déflecteur thermique ajouté sur la nacelle (aléa « surchauffe critique » poste 27)"},
    {"eco": "ECO-002", "code": "D144", "rev": "A", "from_case": 20,
     "motif": "Correction de nomenclature : D144 remplace le second D145 au poste 16"},
    {"eco": "ECO-003", "code": "A423", "rev": "B", "from_case": 30,
     "motif": "Renfort de l'axe du train principal"},
]
LOT_SIZE_AIRCRAFT = 5


@dataclass
class SimParams:
    n_aircraft: int = 40
    takt_min: float | None = None
    seed: int = 7
    duration_noise: float = 0.08
    rework_by_family: dict = field(default_factory=lambda: dict(REWORK_BY_FAMILY))
    rework_factor: float = 1.0
    rework_share: tuple = (0.4, 0.8)
    p_swap_lights: float = 0.15
    p_lights_rework_after_swap: float = 0.8
    p_part_wait: float = 0.20
    part_wait_hours: tuple = (1.0, 4.0)
    fixed_postes: tuple = ()
    fixed_ratio: float = 1.10
    start: str = "2023-09-05 08:00"


def wm_to_ts(start: pd.Timestamp, wm: float, is_end: bool = False) -> pd.Timestamp:
    """Minutes ouvrées depuis le lancement -> horodatage calendaire (pause la nuit)."""
    day, rem = divmod(wm, DAY_MIN)
    if is_end and rem == 0 and wm > 0:
        day, rem = day - 1, DAY_MIN
    return start.normalize() + pd.Timedelta(days=int(day)) + pd.Timedelta(hours=8) + pd.Timedelta(minutes=float(rem))


def real_event_log(ds: Dataset) -> pd.DataFrame:
    m = ds.mes
    return pd.DataFrame({
        "case_id": "MSN-001",
        "activity": m["activity"],
        "poste": m["poste"],
        "operation": m["operation"],
        "start": m["start"],
        "end": m["end_from_duration"],
        "duration_min": m["real_min"],
        "planned_min": m["planned_min"],
        "queue_min": 0.0,
        "resource": "non tracé",
        "family": m["family"],
        "defect_code": None,
        "alea": m["alea"],
        "is_rework": False,
        "is_wait": False,
        "truncated": m["truncated"],
        "source": "réel",
        "evidence": m["evidence"],
    })


def _station_refs(ds: Dataset, case_n: int) -> dict[int, Counter]:
    refs = {int(r.poste): Counter(r.refs) for r in ds.mes.itertuples()}
    eco2 = next(e for e in ECOS if e["eco"] == "ECO-002")
    if case_n >= eco2["from_case"] and refs[16]["D145"]:
        refs[16]["D145"] -= 1
        refs[16]["D144"] += 1
        refs[16] = +refs[16]
    return refs


def _revision(code: str, case_n: int) -> str:
    rev = "A"
    for e in ECOS:
        if e["code"] == code and case_n >= e["from_case"]:
            rev = e["rev"]
    return rev


def simulate(ds: Dataset, params: SimParams = SimParams()) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(params.seed)
    m = ds.mes.set_index("poste")
    postes = list(m.index)
    planned = m["planned_min"].to_dict()
    ratio = (m["real_min"] / m["planned_min"]).to_dict()
    for p in params.fixed_postes:
        ratio[p] = min(ratio[p], params.fixed_ratio)
    family = m["family"].to_dict()
    takt = params.takt_min or max(planned.values())
    start0 = pd.Timestamp(params.start)

    operators = ds.erp.sort_values("operator_id").groupby("poste")["operator_id"].apply(list).to_dict()
    plm = ds.plm.set_index("code")
    long_lead = plm[(plm["criticality"] == "Critique") & (plm["lead_max_days"] >= 35)]
    wait_at = {}
    for code, row in long_lead.iterrows():
        first = next((p for p in postes if code in m.loc[p, "refs"]), None)
        if first is not None:
            wait_at.setdefault(first, []).append((code, row["lead_max_days"]))

    station_free = {p: 0.0 for p in postes}
    events, trace = [], []

    for k in range(params.n_aircraft):
        case_n = k + 2
        case = f"MSN-{case_n:03d}"
        ready = k * takt
        route = postes.copy()
        swapped = rng.random() < params.p_swap_lights
        if swapped:
            i51, i52 = route.index(51), route.index(52)
            route[i51], route[i52] = route[i52], route[i51]
        refs = _station_refs(ds, case_n)
        seq = 0

        def add(activity, poste, s, e, planned_min, queue, resource, fam, defect, rework, wait, alea=None):
            nonlocal seq
            seq += 1
            events.append({
                "case_id": case, "activity": activity, "poste": poste,
                "operation": m.loc[poste, "operation"] if poste else None,
                "start": wm_to_ts(start0, s), "end": wm_to_ts(start0, e, True),
                "start_wm": s, "end_wm": e, "duration_min": e - s, "planned_min": planned_min,
                "queue_min": queue, "resource": resource, "family": fam, "defect_code": defect,
                "alea": alea, "is_rework": rework, "is_wait": wait, "truncated": False,
                "source": "simulé", "evidence": f"SIM:{case}#{seq:02d}",
            })
            return events[-1]["evidence"]

        for p in route:
            for code, lead in wait_at.get(p, []):
                if rng.random() < params.p_part_wait * lead / 45:
                    w = rng.uniform(*params.part_wait_hours) * 60
                    add(f"Attente pièce {code}", None, ready, ready + w, 0.0, 0.0, None, "LOG",
                        "LOG-APPRO", False, True, f"Rupture bord de ligne {code}")
                    ready += w
            s = max(ready, station_free[p])
            queue = s - ready
            dur = planned[p] * ratio[p] * rng.lognormal(0, params.duration_noise)
            e = s + dur
            ops = operators.get(p, ["non affecté"])
            ev = add(m.loc[p, "activity"], p, s, e, planned[p], queue, ops[k % len(ops)], family[p], None,
                     False, False)
            station_free[p] = ready = e
            for code, qty in refs[p].items():
                trace.append({
                    "case_id": case, "poste": p, "activity": m.loc[p, "activity"], "code": code, "qty": qty,
                    "plm_rev": _revision(code, case_n),
                    "erp_lot": f"LOT-{code}-{(case_n - 2) // LOT_SIZE_AIRCRAFT + 1:03d}",
                    "start": wm_to_ts(start0, s), "source": "simulé", "evidence": ev,
                })

            p_rw = params.rework_by_family.get(family[p], 0.2) * params.rework_factor
            redo = [(p, p_rw)]
            if swapped and p == 51:
                redo.append((52, params.p_lights_rework_after_swap * params.rework_factor))
            for target, prob in redo:
                if rng.random() < prob:
                    s = max(ready, station_free[target])
                    dur = planned[target] * ratio[target] * rng.uniform(*params.rework_share)
                    e = s + dur
                    why = "Dépose des lumières posées avant le câblage" if target != p else None
                    who = operators.get(target, ["non affecté"])
                    add(f"Reprise · {m.loc[target, 'activity']}", target, s, e, 0.0, s - ready,
                        who[k % len(who)], family[target], f"{family[target]}-RW", True, False, why)
                    station_free[target] = ready = e

    sim = pd.DataFrame(events)
    trace_df = pd.DataFrame(trace)
    return sim, trace_df


def real_trace(ds: Dataset) -> pd.DataFrame:
    rows = []
    for r in ds.mes.itertuples():
        for code, qty in Counter(r.refs).items():
            rows.append({
                "case_id": "MSN-001", "poste": r.poste, "activity": r.activity, "code": code, "qty": qty,
                "plm_rev": "A (supposée)", "erp_lot": f"LOT-{code}-000 (synthétique)", "start": r.start,
                "source": "réel", "evidence": r.evidence,
            })
    return pd.DataFrame(rows)


def build_event_log(ds: Dataset, params: SimParams = SimParams()) -> tuple[pd.DataFrame, pd.DataFrame]:
    sim, sim_trace = simulate(ds, params)
    real = real_event_log(ds)
    log = pd.concat([real, sim], ignore_index=True)
    trace = pd.concat([real_trace(ds), sim_trace], ignore_index=True)
    return log, trace


def improved(params: SimParams, **changes) -> SimParams:
    return replace(params, **changes)
