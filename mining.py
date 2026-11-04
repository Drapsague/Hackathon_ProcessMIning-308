"""Process mining sans dépendance lourde : DFG, variantes, goulots, encours, conformité, TRS.

pm4py ferait l'affaire, mais pour 56 activités et quelques milliers d'événements, pandas suffit
et chaque calcul reste lisible pendant la soutenance.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd

from etl import FAMILY_LABELS, Dataset
from simulate import SimParams, build_event_log


def section_of(ds: Dataset) -> dict[int, str]:
    return dict(zip(ds.notice_steps["etape"], ds.notice_steps["section"]))


def base_ops(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["is_rework"] & ~df["is_wait"]]


def case_lead_times(df: pd.DataFrame) -> pd.DataFrame:
    """Temps de cycle par avion, en heures ouvrées. Pour MSN-001 on prend la somme des temps
    réels, l'horodatage étant faussé par les clôtures à 17:00 (DQ-01)."""
    out = []
    for case, g in df.groupby("case_id"):
        if (g["source"] == "réel").all():
            lt = g["duration_min"].sum() / 60
        else:
            lt = (g["end_wm"].max() - g["start_wm"].min()) / 60
        out.append({"case_id": case, "lead_time_h": lt, "work_h": g.loc[~g["is_wait"], "duration_min"].sum() / 60,
                    "wait_h": g.loc[g["is_wait"], "duration_min"].sum() / 60,
                    "queue_h": g["queue_min"].sum() / 60, "source": g["source"].iat[0]})
    return pd.DataFrame(out)


def kpis(df: pd.DataFrame) -> dict:
    base = base_ops(df)
    reworked = df[df["is_rework"]].groupby(["case_id", "poste"]).size()
    n_base = len(base)
    rework_rate = len(reworked) / n_base if n_base else float("nan")
    perf = base["planned_min"].sum() / base["duration_min"].sum()
    work = df.loc[~df["is_wait"], "duration_min"].sum()
    wait = df.loc[df["is_wait"], "duration_min"].sum()
    avail = work / (work + wait) if work + wait else float("nan")
    lt = case_lead_times(df)
    real_only = (df["source"] == "réel").all()
    out = {
        "cases": int(df["case_id"].nunique()),
        "lead_time_h": float(lt["lead_time_h"].mean()),
        "lead_time_first_h": float(lt["lead_time_h"].iloc[0]),
        "lead_time_last_h": float(lt["lead_time_h"].iloc[-1]),
        "planned_h": float(base.groupby("case_id")["planned_min"].sum().mean() / 60),
        "rework_rate": None if real_only else rework_rate,
        "rework_time_share": None if real_only else float(df.loc[df["is_rework"], "duration_min"].sum() / work),
        "performance": float(perf),
        "availability": None if real_only else float(avail),
        "quality": None if real_only else float(1 - rework_rate),
    }
    out["oee"] = None if real_only else out["availability"] * out["performance"] * out["quality"]
    if not real_only:
        sim = df[df["source"] == "simulé"]
        # cadence de sortie en régime établi : écart entre la première et la dernière livraison
        ends = sim.groupby("case_id")["end_wm"].max().sort_values()
        span_days = (ends.iloc[-1] - ends.iloc[0]) / (9 * 60)
        out["throughput_per_day"] = float((len(ends) - 1) / span_days) if span_days else float("nan")
        out["wip_mean"] = float(wip_mean(sim))
    return out


def wip_mean(sim: pd.DataFrame) -> float:
    spans = sim.groupby("case_id").agg(s=("start_wm", "min"), e=("end_wm", "max"))
    horizon = spans["e"].max() - spans["s"].min()
    return float((spans["e"] - spans["s"]).sum() / horizon)


def wip_series(sim: pd.DataFrame) -> pd.DataFrame:
    spans = sim.groupby("case_id").agg(s=("start", "min"), e=("end", "max"))
    points = pd.concat([pd.DataFrame({"t": spans["s"], "d": 1}), pd.DataFrame({"t": spans["e"], "d": -1})])
    points = points.sort_values(["t", "d"])
    points["wip"] = points["d"].cumsum()
    return points[["t", "wip"]]


def station_table(df: pd.DataFrame, ds: Dataset) -> pd.DataFrame:
    sec = section_of(ds)
    base = base_ops(df)
    rw = df[df["is_rework"]]
    horizon = None
    if "end_wm" in df and df["end_wm"].notna().any():
        horizon = df["end_wm"].max() - df["start_wm"].min()
    t = base.groupby("poste").agg(
        activity=("activity", "first"), passages=("case_id", "nunique"),
        planned_min=("planned_min", "mean"), real_min=("duration_min", "mean"),
        overrun_total_min=("duration_min", "sum"), planned_total=("planned_min", "sum"),
        queue_total_min=("queue_min", "sum"), busy=("duration_min", "sum"),
    )
    t["overrun_total_min"] = t["overrun_total_min"] - t["planned_total"]
    t["overrun_pct"] = (t["real_min"] / t["planned_min"] - 1) * 100
    t["reworks"] = rw.groupby("poste").size().reindex(t.index).fillna(0).astype(int)
    t["rework_min"] = rw.groupby("poste")["duration_min"].sum().reindex(t.index).fillna(0)
    if horizon:
        t["utilisation"] = (t["busy"] + t["rework_min"]) / horizon
    t["section"] = [sec.get(int(p), "") for p in t.index]
    t["family"] = ds.mes.set_index("poste")["family"].reindex(t.index)
    return t.drop(columns=["planned_total", "busy"]).reset_index()


def bottlenecks(df: pd.DataFrame, ds: Dataset, k: int = 5) -> pd.DataFrame:
    t = station_table(df, ds)
    key = "queue_total_min" if t["queue_total_min"].sum() > 0 else "overrun_total_min"
    return t.sort_values(key, ascending=False).head(k)


def family_pareto(ds: Dataset) -> pd.DataFrame:
    g = ds.mes.groupby("family").agg(operations=("poste", "count"), overrun_min=("overrun_min", "sum"),
                                     postes=("poste", lambda s: ", ".join(map(str, s))))
    g["share"] = g["overrun_min"] / g["overrun_min"].sum()
    g["label"] = [FAMILY_LABELS[f] for f in g.index]
    return g.sort_values("overrun_min", ascending=False).reset_index()


def overrun_cost(ds: Dataset) -> pd.DataFrame:
    """Coût de main-d'œuvre de la dérive sur MSN-001 : (réel - prévu) x coût horaire moyen du poste.
    Hypothèse : un opérateur par opération (le MES ne dit pas combien)."""
    cost = ds.erp.groupby("poste")["hourly_cost_eur"].mean()
    m = ds.mes[["poste", "activity", "overrun_min", "evidence"]].copy()
    m["hourly_cost_eur"] = m["poste"].map(cost)
    m["overrun_cost_eur"] = m["overrun_min"] / 60 * m["hourly_cost_eur"]
    return m


def _activity_key(row, level: str, sec: dict) -> str:
    if row["is_wait"]:
        return row["activity"]
    if level == "section":
        return sec.get(int(row["poste"]), row["activity"])
    return row["activity"].replace("Reprise · ", "") if row["is_rework"] else row["activity"]


def dfg(df: pd.DataFrame, ds: Dataset, level: str = "section") -> tuple[Counter, dict, Counter]:
    """Graphe des successions directes. Les reprises deviennent des boucles sur leur nœud."""
    sec = section_of(ds)
    edges: Counter = Counter()
    gaps: dict = defaultdict(list)
    loops: Counter = Counter()
    for _, g in df.sort_values(["case_id", "start"]).groupby("case_id"):
        prev, prev_end = "▶ Lancement", None
        for _, r in g.iterrows():
            key = _activity_key(r, level, sec)
            if r["is_rework"]:
                loops[key] += 1
                prev_end = r["end"]
                continue
            if key != prev:
                edges[(prev, key)] += 1
                if prev_end is not None:
                    gaps[(prev, key)].append((r["start"] - prev_end).total_seconds() / 60)
            prev, prev_end = key, r["end"]
        edges[(prev, "■ Livraison")] += 1
    return edges, gaps, loops


def dfg_dot(edges: Counter, loops: Counter, node_heat: dict[str, float] | None = None,
            min_count: int = 1) -> str:
    """DOT pour st.graphviz_chart. node_heat (0-1) colore les nœuds en goulot."""
    nodes = {n for e in edges for n in e}
    max_count = max(edges.values()) if edges else 1
    lines = ["digraph G {", 'rankdir=TB; nodesep=0.25; ranksep=0.3;',
             'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10 fillcolor="#f4f4f5" color="#a1a1aa"];',
             'edge [fontname="Helvetica" fontsize=8 color="#71717a"];']
    for n in sorted(nodes):
        attrs = []
        heat = (node_heat or {}).get(n)
        if heat is not None and heat > 0.05:
            r = int(255 - 40 * heat)
            gch = int(244 - 150 * heat)
            b = int(245 - 190 * heat)
            attrs.append(f'fillcolor="#{r:02x}{gch:02x}{b:02x}"')
        if n.startswith("Attente"):
            attrs.append('shape=ellipse fillcolor="#fef3c7" color="#d97706"')
        if n.startswith(("▶", "■")):
            attrs.append('shape=plaintext fillcolor="white"')
        label = n if not loops.get(n) else f"{n}\\n↺ {loops[n]} reprises"
        lines.append(f'"{n}" [label="{label}" {" ".join(attrs)}];')
    for (a, b), c in edges.items():
        if c < min_count:
            continue
        w = 0.6 + 2.4 * c / max_count
        lines.append(f'"{a}" -> "{b}" [label="{c}" penwidth={w:.2f}];')
    lines.append("}")
    return "\n".join(lines)


def variants(df: pd.DataFrame, ds: Dataset, ignore_rework: bool = True) -> pd.DataFrame:
    """Variantes au niveau section. Sans les reprises, on voit les vrais écarts de séquence."""
    sec = section_of(ds)
    rows = []
    lt = case_lead_times(df).set_index("case_id")["lead_time_h"]
    for case, g in df.sort_values(["case_id", "start"]).groupby("case_id"):
        seq = []
        for _, r in g.iterrows():
            if r["is_rework"] and ignore_rework:
                continue
            key = _activity_key(r, "section", sec)
            if r["is_rework"]:
                key = f"↺ {key}"
            if not seq or seq[-1] != key:
                seq.append(key)
        rows.append({"case_id": case, "variant": " → ".join(seq), "lead_time_h": lt[case]})
    v = pd.DataFrame(rows)
    out = v.groupby("variant").agg(cases=("case_id", "count"), lead_time_h=("lead_time_h", "mean"),
                                   exemples=("case_id", lambda s: ", ".join(s.head(3)))).reset_index()
    out["share"] = out["cases"] / out["cases"].sum()
    ref = out.sort_values("cases", ascending=False)["variant"].iat[0].split(" → ")
    out["écart vs variante principale"] = out["variant"].map(lambda s: _diff_summary(ref, s.split(" → ")))
    return out.sort_values("cases", ascending=False).reset_index(drop=True)


def _diff_summary(ref: list[str], seq: list[str]) -> str:
    if ref == seq:
        return "variante principale"
    moves = align(seq, ref)
    extra = [a for kind, a in moves if kind == "log"]
    missing = [a for kind, a in moves if kind == "model"]
    parts = []
    if extra:
        parts.append("en plus : " + ", ".join(extra))
    if missing:
        parts.append("déplacé/absent : " + ", ".join(missing))
    return " ; ".join(parts) or "ordre différent"


def align(trace: list[str], model: list[str]) -> list[tuple[str, str]]:
    """Alignement par distance d'édition : 'sync', 'log' (en trop dans la trace), 'model' (manquant)."""
    n, m = len(trace), len(model)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] if trace[i - 1] == model[j - 1] else 10 ** 9)
    moves, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and trace[i - 1] == model[j - 1] and d[i][j] == d[i - 1][j - 1]:
            moves.append(("sync", trace[i - 1])); i -= 1; j -= 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            moves.append(("log", trace[i - 1])); i -= 1
        else:
            moves.append(("model", model[j - 1])); j -= 1
    return moves[::-1]


def fitness(trace: list[str], model: list[str]) -> float:
    moves = align(trace, model)
    cost = sum(1 for k, _ in moves if k != "sync")
    return 1 - cost / (len(trace) + len(model))


def conformance(df: pd.DataFrame, ds: Dataset) -> pd.DataFrame:
    """Chaque avion contre la gamme de l'outil (ordre des postes du MES, identique à la notice)."""
    model = [f"P{p:02d}" for p in ds.mes.sort_values("poste")["poste"]]
    rows = []
    for case, g in df.sort_values(["case_id", "start"]).groupby("case_id"):
        trace = [f"P{int(p):02d}" for p in base_ops(g)["poste"]]
        extra = g[g["is_rework"] | g["is_wait"]]
        moves = align(trace, model)
        moved = [a for k, a in moves if k == "model"]
        rows.append({
            "case_id": case, "source": g["source"].iat[0],
            "fitness_ordre": fitness(trace, model),
            "postes_hors_ordre": ", ".join(moved) or "aucun",
            "reprises": int(g["is_rework"].sum()), "attentes_pieces": int(g["is_wait"].sum()),
            "fitness_avec_ajouts": fitness([*trace, *["X"] * len(extra)], model),
            "temps_reel_vs_prevu": base_ops(g)["duration_min"].sum() / base_ops(g)["planned_min"].sum(),
        })
    return pd.DataFrame(rows)


def three_versions(ds: Dataset) -> pd.DataFrame:
    """Étape par étape : la notice (documenté), la gamme MES (outil), MSN-001 (observé)."""
    n = ds.notice_steps.set_index("etape")
    m = ds.mes.set_index("poste")
    rows = []
    for step in n.index:
        r = m.loc[step]
        rows.append({
            "étape": step,
            "notice : section": n.loc[step, "section"],
            "notice : page": int(n.loc[step, "page_notice"]),
            "notice : pièces listées": n.loc[step, "liste_pieces"],
            "outil : opération MES": r["operation"],
            "outil : prévu (min)": r["planned_min"],
            "outil : nb références": len(r["refs"]),
            "observé : réel (min)": round(r["real_min"], 1),
            "observé : écart (%)": round(r["overrun_pct"], 0),
            "observé : aléa déclaré": r["alea"],
            "observé : fin tronquée": bool(r["truncated"]),
            "preuves": f"{n.loc[step, 'evidence']} · {r['evidence']}",
        })
    return pd.DataFrame(rows)


def run_scenarios(ds: Dataset, base: SimParams, better: SimParams):
    log_a, _ = build_event_log(ds, base)
    log_b, _ = build_event_log(ds, better)
    sim_a = log_a[log_a["source"] == "simulé"]
    sim_b = log_b[log_b["source"] == "simulé"]
    return kpis(sim_a), kpis(sim_b), sim_a, sim_b


def improvement_params(ds: Dataset, base: SimParams, top_k: int = 5, fixed_ratio: float = 1.10,
                       rework_factor: float = 0.6, enforce_order: bool = True, secure_parts: bool = True,
                       rebalance_takt: bool = True) -> SimParams:
    """Plan d'action traduit en paramètres de simulation.

    - traiter les aléas des top_k postes qui perdent le plus de minutes sur MSN-001 ;
    - réduire les reprises (contrôle au poste, capteurs recalibrés) ;
    - imposer faisceaux avant lumières (détrompage dans le MES) ;
    - sécuriser le bord de ligne des pièces critiques à long délai ;
    - recaler le takt sur le temps réel du goulot au lieu du temps prévu.
    """
    m = ds.mes.set_index("poste")
    top = tuple(int(p) for p in m["overrun_min"].sort_values(ascending=False).head(top_k).index)
    ratio = m["real_min"] / m["planned_min"]
    for p in top:
        ratio[p] = min(ratio[p], fixed_ratio)
    takt = float((m["planned_min"] * ratio).max() * 1.05) if rebalance_takt else base.takt_min
    return SimParams(
        n_aircraft=base.n_aircraft, seed=base.seed, duration_noise=base.duration_noise,
        fixed_postes=top, fixed_ratio=fixed_ratio,
        rework_factor=base.rework_factor * rework_factor,
        p_swap_lights=0.0 if enforce_order else base.p_swap_lights,
        p_part_wait=base.p_part_wait * (0.25 if secure_parts else 1.0),
        takt_min=takt, start=base.start,
    )
