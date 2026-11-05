"""Assistant à preuves : quatre agents, aucune phrase sans source. Il répond en anglais et comprend les
questions en anglais ou en français.

  Analyst    lit la question, appelle des calculs déterministes (pandas) qui renvoient des faits
             déjà chiffrés, chacun avec ses identifiants de preuve ;
  Librarian  complète par une recherche BM25 dans le corpus (lignes MES, PLM, ERP agrégé,
             pages de la notice, constats qualité) ;
  Writer     formule la réponse : un LLM si une clé est configurée, sinon un gabarit ;
  Checker    vérifie chaque phrase : au moins une citation, citations présentes dans le dossier
             de preuves, chiffres présents dans les preuves citées, mention « simulated » si la preuve
             est simulée. Une phrase qui échoue est retirée ; s'il ne reste rien, on refuse.

Le LLM ne voit que des faits déjà calculés (matricules ERP, jamais les noms) et ne calcule rien.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd
import requests

try:  # clés lues depuis un fichier .env s'il existe (jamais commité)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import mining as M
import rules as R
from etl import FAMILY_LABELS, PART_CODE, Dataset, strip_accents

REFUSAL = "INSUFFICIENT DATA"


@dataclass
class Evidence:
    id: str
    text: str
    source: str
    simulated: bool = False


@dataclass
class Fact:
    claim: str
    ids: list[str]
    simulated: bool = False


@dataclass
class Sentence:
    text: str
    ids: list[str]
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    interpretation: bool = False


@dataclass
class Answer:
    question: str
    text: str
    refused: bool
    sentences: list[Sentence]
    evidence: dict[str, Evidence]
    steps: list[tuple[str, str]]
    mode: str


def _fmt(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{x:.0f}"


def build_corpus(ds: Dataset, log: pd.DataFrame, trace: pd.DataFrame) -> dict[str, Evidence]:
    ev: dict[str, Evidence] = {}

    def put(e: Evidence):
        ev[e.id] = e

    for r in ds.mes.itertuples():
        refs = ", ".join(f"{c} x{n}" for c, n in Counter(r.refs).items()) or "none"
        put(Evidence(r.evidence, (
            f"Station {r.poste} \"{r.operation}\": planned {_fmt(r.planned_min)} min, actual {_fmt(r.real_min, 2)} min "
            f"(+{_fmt(r.overrun_min, 2)} min, +{_fmt(r.overrun_pct, 0)}%). Incident \"{r.alea}\", declared cause "
            f"\"{r.cause}\", family {r.family}. References: {refs}. Start {r.start:%d/%m %H:%M:%S}, declared end "
            f"{r.end_declared:%H:%M:%S}" + (", end capped at 17:00." if r.truncated else ".")), "Actual MES"))
    for r in ds.plm.itertuples():
        put(Evidence(r.evidence, (
            f"{r.code} {r.designation}: {r.qty_bom} in the bill of materials, supplier {r.supplier}, lead time "
            f"{r.lead_time}, criticality {r.criticality}, {_fmt(r.mass_kg, 2)} kg, €{r.unit_cost_eur:.0f}, "
            f"CAD time {r.cad_hours:.0f} h (Excel row {r.plm_line})."), "PLM"))
    for poste, g in ds.erp.groupby("poste"):
        levels = ", ".join(f"{k} {v}" for k, v in g["level"].value_counts().items())
        put(Evidence(f"ERP:P{poste}", (
            f"Station {poste}: {len(g)} assigned operators, employee IDs {', '.join(sorted(g['operator_id']))}, "
            f"average hourly cost €{_fmt(g['hourly_cost_eur'].mean(), 2)}, levels: {levels}."), "ERP"))
    for r in ds.notice_steps.itertuples():
        put(Evidence(r.evidence, (
            f"Meccano manual page {r.page_notice}, step {r.etape}: section {r.section}, parts list "
            f"{r.liste_pieces}." + (f" {r.note}." if isinstance(r.note, str) and r.note else "")), "Manual"))
    inv = ds.notice_inv
    put(Evidence("NOTICE:p43", "Manual inventory, page 43: " + ", ".join(
        f"{c or 'sticker sheet'} x{q}" for c, q in zip(inv["code"], inv["quantite"])) + ".", "Manual"))
    for code, n in ds.cad.items():
        put(Evidence(f"CAD:{code}", f"3D model: reference {code} appears {n} times in the product tree.", "CAD"))
    for f in ds.findings:
        put(Evidence(f.id, f"{f.title}. {f.detail}", "Quality check"))
    from simulate import ECOS
    has_sim = bool((log["source"] == "simulé").any())
    for e in ECOS if has_sim else []:
        put(Evidence(e["eco"], f"{e['eco']} (fictional): {e['code']} moves to revision {e['rev']} from "
                               f"MSN-{e['from_case']:03d}. {e['motif']}.", "Simulated PLM", True))
    return ev


def raw_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", strip_accents(text.lower()))


def stem(word: str) -> str:
    return word.rstrip("sx")[:5]


GENERIC_STEMS = {"assem", "fixat", "monta", "parti", "basse", "avion", "droit", "gauch", "left", "right",
                 "aircr", "secti", "attac"}

# Vocabulaire qui situe une question dans le périmètre (racines sans accent, français et anglais)
DOMAIN_STEMS = {
    "avion", "assem", "monta", "poste", "opera", "piece", "refer", "nomen", "fourn", "delai", "retar", "cycle",
    "prevu", "reel", "alea", "cause", "defau", "repri", "quali", "contr", "proce", "gamme", "notic", "etape",
    "mes", "plm", "erp", "lot", "revis", "stock", "encou", "takt", "caden", "trs", "oee", "cout", "equip",
    "compe", "habil", "forma", "forme", "salar", "ligne", "ateli", "usine", "produ", "maque", "cao", "simul",
    "donne", "anoma", "indic", "kpi", "recom", "ameli", "goulo", "air", "airpl", "msn", "derive", "deriv",
    "aile", "fusel", "queue", "cockp", "train", "react", "moteu", "nez", "stick", "marqu", "viss", "ecrou",
    "rivet", "lumie", "faisc", "elect", "conso", "manqu", "ecart", "perfo", "rgpd", "perso",
    "aircr", "plane", "stati", "part", "bill", "suppl", "lead", "delay", "late", "plann", "actua", "incid",
    "defec", "rewor", "check", "routi", "manua", "step", "batch", "wip", "cost", "team", "worke", "emplo",
    "skill", "certi", "line", "shop", "facto", "model", "cad", "data", "recomm", "impro", "bottl", "wing",
    "tail", "gear", "engin", "nose", "screw", "light", "harne", "consu", "missi", "gap", "gdpr", "sever",
    "criti", "prior", "score", "overr", "finan", "price", "expen", "budge", "money", "slow",
}

# Mots anglais de la question -> mots des libellés MES (en français dans les fichiers sources)
ZONE_EN = {
    "wing": "aile", "wings": "aile", "tail": "queue", "landing": "train", "gear": "train", "engine": "reacteur",
    "engines": "reacteur", "reactor": "reacteur", "nose": "nez", "fuselage": "fuselage", "cockpit": "cockpit",
    "wiring": "faisceaux", "harness": "faisceaux", "left": "gauche", "right": "droite", "screws": "visserie",
    "fasteners": "visserie",
}


def en_to_fr(question: str) -> str:
    extra = [ZONE_EN[w] for w in raw_tokens(question) if w in ZONE_EN]
    return question + (" " + " ".join(extra) if extra else "")


STOP = set("le la les de des du un une et en a au aux pour par sur dans est sont quel quelle quels quelles "
           "qui que quoi comment pourquoi ce cette ces il elle on nous vous ils plus moins avec sans se sa son "
           "ses leur leurs ou ne pas y d l qu c s excel ligne lignes "
           "the an of to in on for is are what which how why this that these those with without by from at it "
           "be do does did was were has have most least more less there".split())

STATION = r"(?:poste|station)\s*(\d{1,2})|\bp(\d{2})\b"


def tokens(text: str) -> list[str]:
    t = re.findall(r"[a-z0-9]+", strip_accents(text.lower()))
    return [w for w in t if len(w) > 1 and w not in STOP]


class BM25:
    def __init__(self, docs: dict[str, Evidence], k1: float = 1.4, b: float = 0.75):
        self.ids = list(docs)
        self.toks = [tokens(docs[i].text + " " + i) for i in self.ids]
        self.avg = sum(map(len, self.toks)) / max(len(self.toks), 1)
        df = Counter(w for t in self.toks for w in set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(t) for t in self.toks]
        self.k1, self.b = k1, b

    def search(self, query: str, k: int = 6) -> list[tuple[str, float]]:
        q = tokens(query)
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            dl = len(self.toks[i])
            for w in q:
                if w in tf:
                    s += self.idf[w] * tf[w] * (self.k1 + 1) / (tf[w] + self.k1 * (1 - self.b + self.b * dl / self.avg))
            if s > 0:
                scores.append((self.ids[i], s))
        return sorted(scores, key=lambda x: -x[1])[:k]


class Analyst:
    """Calculs déterministes appelés selon la question. Chaque fait porte ses preuves."""

    def __init__(self, ds: Dataset, log: pd.DataFrame, trace: pd.DataFrame, evidence: dict[str, Evidence],
                 impact: tuple[dict, dict] | None = None, res: R.Resultats | None = None):
        self.ds, self.log, self.trace, self.ev = ds, log, trace, evidence
        self.real = log[log["source"] == "réel"]
        self.sim = log[log["source"] == "simulé"]
        self.has_sim = len(self.sim) > 0
        self.impact = impact
        self.res = res or R.evaluer(ds)

    def stat(self, sid: str, text: str, simulated: bool = False) -> str:
        self.ev[sid] = Evidence(sid, text, "Simulated calculation" if simulated else "Calculation on actual data",
                                simulated)
        return sid

    def overview(self) -> list[Fact]:
        k = M.kpis(self.real)
        pct = _fmt((k['lead_time_h'] / k['planned_h'] - 1) * 100, 0)
        sid = self.stat("STAT:cycle_msn001", (
            f"MSN-001: sum of actual times {_fmt(k['lead_time_h'])} h, sum of planned times {_fmt(k['planned_h'])} h, "
            f"i.e. +{pct}%. Performance (planned/actual) {_fmt(k['performance'] * 100, 1)}%. All 56 operations "
            f"overrun."))
        return [Fact(f"On MSN-001, assembly took {_fmt(k['lead_time_h'])} actual hours for {_fmt(k['planned_h'])} "
                     f"planned hours, i.e. +{pct}%, and all 56 operations exceeded their planned time.", [sid])]

    def bottlenecks(self) -> list[Fact]:
        top = self.ds.mes.sort_values("overrun_min", ascending=False).head(3)
        facts = []
        for i, r in enumerate(top.itertuples()):
            lead = (f"On MSN-001, station {r.poste} ({r.operation}) loses the most time" if i == 0
                    else f"Next comes station {r.poste} ({r.operation})")
            facts.append(Fact(f"{lead}: {_fmt(r.real_min, 2)} actual minutes for {_fmt(r.planned_min)} planned "
                              f"(+{_fmt(r.overrun_min, 2)} min).", [r.evidence]))
        b = M.bottlenecks(self.sim, self.ds, 2) if self.has_sim else pd.DataFrame()
        for r in b.itertuples():
            sid = self.stat(f"SIM:queue_P{int(r.poste):02d}", (
                f"Simulation of 40 aircraft: cumulative queue in front of station {int(r.poste)} "
                f"{_fmt(r.queue_total_min / 60, 0)} h, utilisation {_fmt(r.utilisation * 100, 0)}%."), True)
            facts.append(Fact(f"In the simulated fleet, the cumulative queue in front of station {int(r.poste)} "
                              f"reaches {_fmt(r.queue_total_min / 60, 0)} h.", [sid], True))
        return facts

    def station(self, poste: int) -> list[Fact]:
        m = self.ds.mes.set_index("poste")
        if poste not in m.index:
            return []
        r = m.loc[poste]
        facts = [Fact(f"Station {poste} ({r['operation']}) took {_fmt(r['real_min'], 2)} min for "
                      f"{_fmt(r['planned_min'])} planned, with the declared incident \"{r['alea']}\".", [r["evidence"]])]
        if r["truncated"]:
            facts.append(Fact("Its end time is capped at 17:00 although the declared duration goes beyond it: when the "
                              "work actually finished is unknown.", [r["evidence"], "DQ-01"]))
        step = self.ds.notice_steps.set_index("etape").loc[poste]
        facts.append(Fact(f"In the manual, this is step {poste} on page {step['page_notice']}, section "
                          f"{step['section']}.", [step["evidence"]]))
        if f"ERP:P{poste}" in self.ev:
            mats = sorted(self.ds.erp.loc[self.ds.erp["poste"] == poste, "operator_id"])
            facts.append(Fact(f"The ERP assigns {len(mats)} operators to this station: {', '.join(mats)}.",
                              [f"ERP:P{poste}"]))
        o = self.res.operations.set_index("poste").loc[poste]
        sid = self.stat(f"STAT:score_P{poste:02d}", (
            f"Station {poste}: delay {_fmt(o['retard_pct'], 1)}%, severity {o['gravite']} ({o['pts_retard']} points), "
            f"incident {o['pts_incident']} points, critical part {o['pts_critique']} points, long lead time "
            f"{o['pts_appro']} points, score {o['score']}/100, priority {o['priorite']}"
            + (f", provisional score ({o['limites_score']})" if o["score_provisoire"] else "") + "."))
        facts.append(Fact(f"Its investigation score is {o['score']}/100, {o['priorite'].lower()} priority "
                          f"({o['gravite']} delay, incident, critical part and long lead time counted as per the "
                          f"specification)" + (", provisional score" if o["score_provisoire"] else "") + ".", [sid]))
        st = M.station_table(self.sim, self.ds).set_index("poste") if self.has_sim else pd.DataFrame()
        if poste in st.index:
            s = st.loc[poste]
            sid = self.stat(f"SIM:poste_P{poste:02d}", (
                f"Simulation: station {poste}, {int(s['reworks'])} reworks over {int(s['passages'])} aircraft, "
                f"cumulative queue {_fmt(s['queue_total_min'] / 60, 0)} h."), True)
            facts.append(Fact(f"In the simulation, this station has {int(s['reworks'])} reworks over "
                              f"{int(s['passages'])} aircraft.", [sid], True))
        return facts

    def part(self, code: str) -> list[Fact]:
        if f"PLM:{code}" not in self.ev:
            return []
        p = self.ds.plm.set_index("code").loc[code]
        bom = self.ds.bom.set_index("code").loc[code]
        uses = self.ds.mes[self.ds.mes["refs"].map(lambda r: code in r)]
        facts = [Fact(f"{code} ({p['designation']}) appears {int(p['qty_bom'])} time(s) in the bill of materials, "
                      f"supplied by {p['supplier']} within {p['lead_time']}, criticality {p['criticality']}.",
                      [f"PLM:{code}"])]
        if len(uses):
            sid = self.stat(f"STAT:conso_{code}", (
                f"MSN-001: {code} appears {int(bom['mes'])} time(s) in the MES (bill of materials {int(p['qty_bom'])}), "
                f"stations {', '.join(map(str, uses['poste']))} (rows {', '.join(uses['evidence'])})."))
            facts.append(Fact(f"The MES consumes it {int(bom['mes'])} time(s) on MSN-001, at stations "
                              f"{', '.join(map(str, uses['poste']))}.", [sid] + uses["evidence"].tolist()[:3]))
        else:
            facts.append(Fact("The MES never consumes it on MSN-001.", ["DQ-04"]))
        if f"CAD:{code}" in self.ev:
            facts.append(Fact(f"The 3D model contains it {int(self.ds.cad[code])} time(s).", [f"CAD:{code}"]))
        for f in self.ds.findings:
            if code in f.detail and f.id in ("DQ-03", "DQ-05", "DQ-06"):
                facts.append(Fact(f"Point of attention: {f.title}.", [f.id]))
        t = self.trace[(self.trace["code"] == code) & (self.trace["source"] == "simulé")]
        if len(t):
            revs = ", ".join(sorted(t["plm_rev"].unique()))
            sid = self.stat(f"SIM:trace_{code}", (
                f"Simulation: {code} traced over {t['case_id'].nunique()} aircraft, revisions {revs}, "
                f"{t['erp_lot'].nunique()} ERP batches."), True)
            facts.append(Fact(f"In the simulation, it is traced over {t['case_id'].nunique()} aircraft with "
                              f"{t['erp_lot'].nunique()} ERP batches and revisions {revs}.", [sid], True))
        return facts

    def causes(self) -> list[Fact]:
        fp = M.family_pareto(self.ds)
        top3 = fp.head(3)
        share = top3["share"].sum() * 100
        sid = self.stat("STAT:familles", "Minutes lost on MSN-001 by incident family: " + "; ".join(
            f"{r.family} ({r.label}) {_fmt(r.overrun_min, 0)} min, {_fmt(r.share * 100, 0)}%, {r.operations} operations"
            for r in fp.itertuples()) + ".")
        return [
            Fact(f"Three incident families account for {_fmt(share, 0)}% of the minutes lost on MSN-001: "
                 + ", ".join(f"{r.label.lower()} ({_fmt(r.share * 100, 0)}%)" for r in top3.itertuples()) + ".",
                 [sid]),
            Fact("These causes are declared, one per operation, and are not verified: they are leads, not root "
                 "causes.", ["DQ-08"]),
        ]

    def priorities(self) -> list[Fact]:
        o = self.res.operations
        top = o.sort_values(["score", "retard_pct"], ascending=False).head(5)
        counts = o["priorite"].value_counts()
        sid = self.stat("STAT:priorites", (
            f"P04 investigation score over {len(o)} operations: high priority {counts.get('High', 0)}, medium "
            f"{counts.get('Medium', 0)}, low {counts.get('Low', 0)}, provisional scores "
            f"{int(o['score_provisoire'].sum())}. Top five: " + "; ".join(
                f"station {r.poste} {r.score}/100" for r in top.itertuples()) + "."))
        low = counts.get("Low", 0)
        facts = [Fact(f"The specification's score puts {counts.get('High', 0)} operations in high priority and "
                      f"{counts.get('Medium', 0)} in medium priority; "
                      + ("none is in low priority." if not low else f"{low} are in low priority."), [sid])]
        facts.append(Fact("To investigate first: " + ", ".join(
            f"station {r.poste} ({r.score}/100)" for r in top.itertuples()) + ".", [sid]))
        facts.append(Fact("The score shows where to investigate; it does not identify a cause.", ["STAT:priorites"]))
        return facts

    def gravity(self) -> list[Fact]:
        k = self.res.kpi
        crit = self.res.operations[self.res.operations["gravite"] == "critical"]
        sid = self.stat("STAT:gravite", (
            f"MES03 severity over {k['operations']} operations: {k['retards_importants']} major (20 to 50%), "
            f"{k['retards_critiques']} critical (50% and above), cumulative delay {_fmt(k['retard_cumule_min'], 1)} min. "
            f"Critical stations: {', '.join(map(str, crit['poste']))}."))
        return [Fact(f"Out of {k['operations']} operations, {k['retards_importants']} have a major delay and "
                     f"{k['retards_critiques']} a critical delay, for {_fmt(k['retard_cumule_min'], 1)} min of "
                     f"cumulative overrun.", [sid]),
                Fact(f"Critical delays affect stations {', '.join(map(str, crit['poste']))}.", [sid])]

    def rework(self) -> list[Fact]:
        if not self.has_sim:
            return [Fact("The actual data contains no defect code and no rework: the rework rate cannot be "
                         "measured.", ["DQ-09"])]
        k = M.kpis(self.sim)
        sid = self.stat("SIM:reprises", (
            f"Simulation of 40 aircraft: rework rate {_fmt(k['rework_rate'] * 100, 0)}% of operations, "
            f"{_fmt(k['rework_time_share'] * 100, 0)}% of working time."), True)
        return [
            Fact("The actual data contains no defect code and no rework: the actual rework rate cannot be "
                 "measured.", ["DQ-09"]),
            Fact(f"In the simulation, calibrated on the 25% stated in the brief, {_fmt(k['rework_rate'] * 100, 0)}% "
                 f"of operations are reworked, i.e. {_fmt(k['rework_time_share'] * 100, 0)}% of working time.",
                 [sid], True),
        ]

    def oee(self) -> list[Fact]:
        kr = M.kpis(self.real)
        sid_r = self.stat("STAT:performance", f"MSN-001: performance (planned time / actual time) "
                                              f"{_fmt(kr['performance'] * 100, 1)}%.")
        if not self.has_sim:
            return [Fact(f"On MSN-001, only performance can be measured: {_fmt(kr['performance'] * 100, 1)}%.",
                         [sid_r]),
                    Fact("Quality and availability are missing, for lack of defect codes and recorded stoppages: OEE "
                         "cannot be computed.", ["DQ-09"])]
        ks = M.kpis(self.sim)
        sid_s = self.stat("SIM:trs", (
            f"Simulation: OEE {_fmt(ks['oee'] * 100, 1)}% = availability {_fmt(ks['availability'] * 100, 1)}% x "
            f"performance {_fmt(ks['performance'] * 100, 1)}% x quality {_fmt(ks['quality'] * 100, 1)}%."), True)
        return [
            Fact(f"On MSN-001, only performance can be measured: {_fmt(kr['performance'] * 100, 1)}%.", [sid_r]),
            Fact("Actual quality and availability are missing, for lack of defect codes and recorded stoppages.",
                 ["DQ-09"]),
            Fact(f"The simulated OEE is {_fmt(ks['oee'] * 100, 1)}%.", [sid_s], True),
        ]

    def wip(self) -> list[Fact]:
        planned_max = self.ds.mes["planned_min"].max()
        real_max = self.ds.mes["real_min"].max()
        first = Fact(f"The longest station is planned at {_fmt(planned_max, 0)} min but takes {_fmt(real_max, 2)} min "
                     f"on MSN-001.", ["MES:L32"])
        if not self.has_sim:
            return [first, Fact("A single aircraft is traced: work in progress cannot be measured with this data.",
                                ["DQ-09"])]
        k = M.kpis(self.sim)
        sid = self.stat("SIM:encours", (
            f"Simulation: launch every {_fmt(planned_max, 0)} min (planned time of the longest station), "
            f"average WIP {_fmt(k['wip_mean'], 1)} aircraft, cycle time of the first aircraft "
            f"{_fmt(k['lead_time_first_h'], 1)} h, of the last {_fmt(k['lead_time_last_h'], 1)} h."), True)
        return [
            first,
            Fact(f"In simulation, launching an aircraft every {_fmt(planned_max, 0)} min grows the cycle time from "
                 f"{_fmt(k['lead_time_first_h'], 1)} h for the first aircraft to {_fmt(k['lead_time_last_h'], 1)} h "
                 f"for the last, with {_fmt(k['wip_mean'], 1)} aircraft in progress on average.", [sid], True),
        ]

    def zone(self, question: str) -> list[Fact]:
        """Postes dont le libellé partage un mot avec la question (« wings », « cockpit », « ailes »...)."""
        q = {stem(t) for t in tokens(en_to_fr(question)) if len(t) >= 4} - GENERIC_STEMS
        m = self.ds.mes
        hit = m[m["operation"].map(lambda op: bool(q & {stem(t) for t in tokens(op) if len(t) >= 4}))]
        if hit.empty:
            return []
        if len(hit) == 1:
            return self.station(int(hit["poste"].iat[0]))
        worst = hit.loc[hit["overrun_min"].idxmax()]
        words = ", ".join(sorted(q & {stem(t) for op in hit["operation"] for t in tokens(op)}))
        pct = _fmt((hit['real_min'].sum() / hit['planned_min'].sum() - 1) * 100, 0)
        sid = self.stat(f"STAT:zone_{words.replace(', ', '_')}", (
            f"MSN-001, operations whose label contains \"{words}\": {len(hit)} stations "
            f"({', '.join(map(str, hit['poste']))}), planned {_fmt(hit['planned_min'].sum())} min, actual "
            f"{_fmt(hit['real_min'].sum(), 2)} min, i.e. +{pct}%."))
        return [
            Fact(f"On MSN-001, the {len(hit)} operations concerned (stations {', '.join(map(str, hit['poste']))}) took "
                 f"{_fmt(hit['real_min'].sum(), 2)} min for {_fmt(hit['planned_min'].sum())} planned, i.e. +{pct}%.",
                 [sid]),
            Fact(f"The most delayed is station {worst['poste']} ({worst['operation']}): {_fmt(worst['real_min'], 2)} min "
                 f"for {_fmt(worst['planned_min'])} planned, incident \"{worst['alea']}\".", [worst["evidence"]]),
        ]

    def supplier(self, question: str) -> list[Fact]:
        q = {stem(t) for t in raw_tokens(question) if len(t) >= 4} - GENERIC_STEMS
        p = self.ds.plm
        # on garde les fournisseurs qui partagent le plus de mots avec la question (« France » seul ne suffit pas)
        score = p["supplier"].map(lambda sup: len(q & {stem(t) for t in raw_tokens(sup) if len(t) >= 4}))
        hit = p[(score > 0) & (score == score.max())]
        facts = []
        for sup, g in hit.groupby("supplier"):
            used = self.ds.bom.set_index("code").reindex(g["code"])
            gaps = used[used["mes_vs_plm"] != 0].index.tolist()
            sid = self.stat(f"STAT:fournisseur_{'_'.join(raw_tokens(sup))}", (
                f"{sup}: {len(g)} references ({', '.join(g['code'])}), lead times {', '.join(sorted(set(g['lead_time'])))}, "
                f"criticalities {', '.join(sorted(set(g['criticality'])))}, MES / PLM gaps on {', '.join(gaps) or 'none'}."))
            refs = f"{len(g)} reference{'s' if len(g) > 1 else ''}"
            facts.append(Fact(f"{sup} supplies {refs} ({', '.join(g['code'])}), with a lead time of "
                              f"{', '.join(sorted(set(g['lead_time'])))}.", [sid]))
            facts.append(Fact(f"Gap between bill of materials and MES consumption on: {', '.join(gaps)}." if gaps else
                              f"No gap between bill of materials and MES consumption for {sup}.", [sid]))
        return facts

    def workforce(self) -> list[Fact]:
        e = self.ds.erp
        levels = ", ".join(f"{k} {v}" for k, v in e["level"].value_counts().items())
        sid = self.stat("STAT:effectifs", (
            f"ERP: {len(e)} operators over {e['poste'].nunique()} stations, levels: {levels}, average hourly cost "
            f"€{_fmt(e['hourly_cost_eur'].mean(), 2)}, from €{_fmt(e['hourly_cost_eur'].min(), 2)} to "
            f"€{_fmt(e['hourly_cost_eur'].max(), 2)}."))
        return [
            Fact(f"The ERP lists {len(e)} operators across {e['poste'].nunique()} stations, with levels: {levels}.", [sid]),
            Fact("The MES does not say who carried out each operation: a level or a training cannot be linked to a "
                 "delay.", ["DQ-09"]),
        ]

    def data_quality(self) -> list[Fact]:
        sev = [f for f in self.ds.findings if f.severity == "haute"]
        return [Fact(f"{f.title}: {f.detail.split('. ')[0].rstrip('.')}.", [f.id]) for f in sev[:5]]

    def conformance(self) -> list[Fact]:
        cf = M.conformance(self.real, self.ds).iloc[0]
        sid = self.stat("STAT:conformite_msn001", (
            f"MSN-001: order fitness {_fmt(cf['fitness_ordre'] * 100, 0)}% against the MES routing, actual / planned "
            f"time ratio {_fmt(cf['temps_reel_vs_prevu'], 2)}."))
        return [
            Fact(f"MSN-001 follows the routing order exactly: fitness {_fmt(cf['fitness_ordre'] * 100, 0)}%.", [sid]),
            Fact("The manual also has 56 steps, in the same section order as the MES.",
                 ["NOTICE:p6-e1", "NOTICE:p42-e56"]),
            Fact("The gap is therefore not in the sequence but in the times and the materials.", [sid, "DQ-03"]),
            Fact("Station 51, with no part, matches manual step 51, which has no parts list.", ["DQ-02"]),
        ]

    def people_postes(self, question: str, q: str) -> list[int]:
        """Postes visés par une question sur les personnes : numéro, référence pièce, zone, sinon le plus en retard."""
        m = self.ds.mes
        postes = [int(a or b) for a, b in re.findall(STATION, q)]
        for code in sorted(set(PART_CODE.findall(question.upper()))):
            postes += m.loc[m["refs"].map(lambda r: code in r), "poste"].tolist()
        if not postes:
            words = {stem(t) for t in tokens(en_to_fr(question)) if len(t) >= 4} - GENERIC_STEMS
            postes = m.loc[m["operation"].map(lambda op: bool(words & {stem(t) for t in tokens(op) if len(t) >= 4})),
                           "poste"].tolist()
        if not postes and re.search(r"retard|lent|faute|responsable|perd|plus long|delay|late|slow|fault|"
                                    r"responsible|los[te]|longest", q):
            postes = m.sort_values("overrun_min", ascending=False)["poste"].head(1).tolist()
        return list(dict.fromkeys(postes))[:6]

    def people(self, question: str, q: str) -> list[Fact]:
        m = self.ds.mes.set_index("poste")
        e = self.ds.erp
        facts = []
        for poste in self.people_postes(question, q):
            if poste not in m.index:
                continue
            r = m.loc[poste]
            mats = sorted(e.loc[e["poste"] == poste, "operator_id"])
            ids = [r["evidence"]] + ([f"ERP:P{poste}"] if mats else [])
            who = f"the ERP assigns employee IDs {', '.join(mats)}" if mats else "the ERP assigns no employee ID"
            facts.append(Fact(f"At station {poste} ({r['operation']}), {who}; the operation took "
                              f"{_fmt(r['real_min'], 2)} min for {_fmt(r['planned_min'])} planned "
                              f"(+{_fmt(r['overrun_min'], 2)} min).", ids))
        if facts:
            facts.append(Fact("The MES does not say which employee carried out the operation: these are the resources "
                              "declared on the station, and the ERP also schedules rotations between stations.",
                              ["DQ-09"]))
        return facts

    def cost(self) -> list[Fact]:
        f, v = self.res.fin, self.res.fin_refs
        top = v.iloc[0]
        sid = self.stat("STAT:fin_pieces", (
            f"FIN01: total cost of required parts €{f['cout_total']:,.0f} (sum of PLM quantity x purchase cost) "
            f"over {f['references_valorisees']} references, {f['donnees_invalides']} invalid data. FIN03: "
            f"{top['code']} weighs {_fmt(f['top_part_pct'], 2)}% of the total, the first two references "
            f"{_fmt(f['top2_part_pct'], 2)}%, {f['refs_80pct']} references exceed 80%."))
        sid2 = self.stat("STAT:fin_postes", (
            f"FIN04: theoretical hourly cost per station from {_fmt(f['fin04_min'], 1)} to {_fmt(f['fin04_max'], 1)} €/h, "
            f"average {_fmt(f['fin04_moyen'], 2)} €/h, assuming the station's resources work simultaneously. FIN05: "
            f"average hourly cost of a resource {_fmt(f['fin05_moyen'], 2)} €/h."))
        return [
            Fact(f"The total cost of the parts needed for one aircraft is €{f['cout_total']:,.0f} over "
                 f"{f['references_valorisees']} valued references.", [sid]),
            Fact(f"{top['code']} ({top['designation']}) alone accounts for {_fmt(f['top_part_pct'], 2)}% of this "
                 f"cost, and {f['refs_80pct']} references are enough to exceed 80%.", [sid, top["evidence"]]),
            Fact(f"A station's theoretical hourly cost ranges from {_fmt(f['fin04_min'], 1)} to "
                 f"{_fmt(f['fin04_max'], 1)} €/h, with an average of {_fmt(f['fin04_moyen'], 2)} €/h.", [sid2]),
            Fact("These are reference costs from the PLM and the ERP, not losses caused by delays: the data does not "
                 "allow the cost of a delay to be quantified.", [sid, sid2]),
        ]

    def suppliers(self) -> list[Fact]:
        p = self.ds.plm
        crit = p[(p["criticality"] == "Critical")].sort_values("lead_max_days", ascending=False).head(4)
        return [Fact(f"{r.code} ({r.designation}) is critical with a lead time of {r.lead_time} from {r.supplier}.",
                     [r.evidence]) for r in crit.itertuples()]

    def recommendations(self) -> list[Fact]:
        self.priorities()  # enregistre STAT:priorites, cité plus bas
        m = self.ds.mes.set_index("poste")
        p31 = m.loc[31]
        facts = [
            Fact(f"Address station 31 first: {_fmt(p31['real_min'], 2)} min for {_fmt(p31['planned_min'])} planned, "
                 f"incident \"{p31['alea']}\".", [p31["evidence"]]),
            Fact("Forbid starting an operation that cannot finish before 17:00, or record its resumption the next day, "
                 "so that declared time is no longer lost.", ["DQ-01"]),
            Fact("Fix the rear tail reference in the MES and the 3D model.", ["DQ-05"]),
            Fact("Base the launch rate on the bottleneck's actual time, not its planned time.", ["MES:L32"]),
            Fact(f"Investigate the {self.res.kpi['priorites_elevees']} high-priority operations of the investigation "
                 f"score first.", ["STAT:priorites"]),
        ]
        fp = M.family_pareto(self.ds).head(3)
        sid = self.stat("STAT:familles_top3", "Costliest incident families on MSN-001: " + ", ".join(
            f"{r.label} {_fmt(r.share * 100, 0)}%" for r in fp.itertuples()) + ".")
        facts.append(Fact("Target maintenance and the shop-floor environment, the families that weigh most in the "
                          "minutes lost.", [sid, "DQ-08"]))
        if self.impact:
            a, b = self.impact
            sid = self.stat("SIM:impact", (
                f"Simulation of the action plan over {a['cases']} aircraft: cycle time {_fmt(a['lead_time_h'], 1)} h -> "
                f"{_fmt(b['lead_time_h'], 1)} h, rework {_fmt(a['rework_rate'] * 100, 0)}% -> "
                f"{_fmt(b['rework_rate'] * 100, 0)}%, OEE {_fmt(a['oee'] * 100, 1)}% -> {_fmt(b['oee'] * 100, 1)}%."), True)
            facts.append(Fact(f"Simulated over {a['cases']} aircraft, this plan brings cycle time from "
                              f"{_fmt(a['lead_time_h'], 1)} h to {_fmt(b['lead_time_h'], 1)} h and OEE from "
                              f"{_fmt(a['oee'] * 100, 1)}% to {_fmt(b['oee'] * 100, 1)}%.", [sid], True))
        return facts


INTENTS = [
    ("priorities", r"priorit|score|examiner|investig|urgen|look at first"),
    ("gravity", r"gravit|severit|retards? (importants?|critiques?)|(major|critical) delays?|"
                r"combien d.operations? en retard|how many (operations )?(are )?(late|delayed)"),
    ("workforce", r"operateurs|equipes?\b|competen|habilit|form[ea]|effectif|qualification|personnel|salaries|"
                  r"operators|teams?\b|workforce|headcount|skills?|certific|staff|employees"),
    ("recommendations", r"recommand|que faire|plan d.?action|ameliorer|levier|conseil|recommend|what should|"
                        r"action plan|improve|advice|suggest"),
    ("cost", r"cout|euro|€|budget|argent|financ|prix|cher|cost|price|expensive|money|spend"),
    ("suppliers", r"fournisseur|supplier|approvisionnement|delai|procure|sourcing|lead.?time"),
    ("bottlenecks", r"goulot|bottleneck|retard|plus long|perd|lent|bloqu|delay|late|longest|slow|los[ei]|block"),
    ("rework", r"reprise|rework|defaut|non.?conform|rebut|defect|scrap"),
    ("oee", r"\btrs\b|\boee\b|rendement|efficiency"),
    ("wip", r"encours|en.?cours|\bwip\b|takt|cadence|stock|work in progress|throughput"),
    ("causes", r"cause|pourquoi|alea|famille|origine|why|incident|famil"),
    ("conformance", r"conform|notice|document|gamme|ecart.*process|sequence|ordre|variante|manual|routing|"
                    r"\border\b|variant"),
    ("data_quality", r"qualite des donnees|incoheren|anomal|erreur|fiab|donnees|data quality|inconsisten|error|"
                     r"reliab|\bdata\b"),
    ("overview", r"temps de cycle|cycle|lead.?time|duree|resume|synthese|combien de temps|duration|summary|"
                 r"overview|how long"),
]

PERSONAL = (r"\bnoms?\b|prenom|\bage\b|identit|adresse|telephone|salaire de|\bnames?\b|first name|surname|"
            r"how old|\baddress|phone|salary of")
PREDICTION = (r"prevo|predi|futur|l.?annee prochaine|en 202[7-9]|dans \d+ (mois|ans)|"
              r"combien .* livr.* (decembre|janvier|mois)|forecast|next year|in 202[7-9]|in \d+ (months|years)|"
              r"how many .* deliver.* (december|january|month)")
PEOPLE = (r"matricule|operateur|technicien|ouvrier|personne|salarie|\bqui\b|responsable|la faute|"
          r"le plus lent|le moins (rapide|efficace|bon)|employee|operator|technician|worker|person|people|\bwho\b|"
          r"responsible|fault|slowest|least (fast|efficient|good)")
MISSING_REAL = r"lot reel|vrai lot|revision reelle|code defaut reel|cause racine|real batch|actual batch|actual revision|real defect code|root cause"


class Writer:
    """Rédacteur : gabarit déterministe ou LLM. Configuration par variables d'environnement :
    AIRPLUS_LLM=anthropic|groq|openai, ANTHROPIC_API_KEY / GROQ_API_KEY / OPENAI_API_KEY, AIRPLUS_LLM_MODEL,
    OPENAI_BASE_URL pour un serveur compatible (Mistral, Ollama, vLLM...)."""

    SYSTEM = (
        "You are the writing agent of a process mining tool for an aerospace manufacturer. Strict rules:\n"
        "1. Use only the facts provided. No outside knowledge.\n"
        "2. Every sentence ends with one or more citations in square brackets, copied from the facts, "
        "for example [MES:L32] or [DQ-01].\n"
        "3. Do not write any figure that does not appear in the facts cited by the sentence. Use a dot as the "
        "decimal separator and no thousands separator, exactly as in the facts.\n"
        "4. If a fact is marked SIMULATED, the sentence using it contains the word \"simulated\" or \"simulation\".\n"
        "5. You may interpret the facts to answer as well as possible, even if they only partly answer: an "
        "interpretation sentence starts with \"Hypothesis:\" or \"Interpretation:\", contains no figures and needs "
        "no citation. Say what is missing to confirm it.\n"
        "6. Answer exactly \"INSUFFICIENT DATA\" followed by one explanatory sentence only if no fact relates to the "
        "question.\n"
        "Answer in English, in 2 to 6 short sentences, with no list or heading. Names of operations, incidents and "
        "parts stay as written in the facts."
    )

    def __init__(self):
        self.last_error = None
        self.provider = os.environ.get("AIRPLUS_LLM", "").lower()
        if not self.provider:
            if os.environ.get("ANTHROPIC_API_KEY"):
                self.provider = "anthropic"
            elif os.environ.get("GROQ_API_KEY"):
                self.provider = "groq"
            elif os.environ.get("OPENAI_API_KEY"):
                self.provider = "openai"

    @property
    def mode(self) -> str:
        return f"LLM ({self.provider})" if self.provider in ("anthropic", "groq", "openai") else "deterministic"

    def template(self, facts: list[Fact]) -> str:
        return " ".join(f"{f.claim} " + "".join(f"[{i}]" for i in f.ids) for f in facts)

    def write(self, question: str, facts: list[Fact], evidence: dict[str, Evidence]) -> str:
        self.last_error = None
        if self.mode == "deterministic":
            return self.template(facts)
        ids = sorted({i for f in facts for i in f.ids})
        lines = [f"- {f.claim} " + "".join(f"[{i}]" for i in f.ids) + (" (SIMULATED)" if f.simulated else "")
                 for f in facts]
        lines += [f"- Evidence [{i}]: {evidence[i].text}" + (" (SIMULATED)" if evidence[i].simulated else "")
                  for i in ids if i in evidence]
        user = f"Question: {question}\n\nAvailable facts:\n" + "\n".join(lines)
        try:
            return self._call(user)
        except Exception as exc:  # pas de réseau, quota, clé invalide : on retombe sur le gabarit
            self.last_error = f"{type(exc).__name__}: {exc}"[:160]
            return self.template(facts)

    def _call(self, user: str) -> str:
        if self.provider == "groq":
            from groq import Groq
            resp = Groq(api_key=os.environ["GROQ_API_KEY"]).chat.completions.create(
                model=os.environ.get("AIRPLUS_LLM_MODEL", "openai/gpt-oss-120b"), temperature=0,
                messages=[{"role": "system", "content": self.SYSTEM}, {"role": "user", "content": user}])
            return resp.choices[0].message.content or ""
        if self.provider == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": os.environ.get("AIRPLUS_LLM_MODEL", "claude-sonnet-5-5"), "max_tokens": 600,
                      "temperature": 0, "system": self.SYSTEM, "messages": [{"role": "user", "content": user}]},
                timeout=60)
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"])
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
            json={"model": os.environ.get("AIRPLUS_LLM_MODEL", "gpt-4o-mini"), "temperature": 0,
                  "messages": [{"role": "system", "content": self.SYSTEM}, {"role": "user", "content": user}]},
            timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


CITE = re.compile(r"\[([A-Za-z]+[:\-][^\[\]]+?)\]")
NUM = re.compile(r"(?<![\w-])\d+(?:[.,]\d+)?")


def _numbers(text: str) -> list[float]:
    clean = CITE.sub(" ", text)
    clean = re.sub(r"MSN-\d+|LOT-\S+|ECO-\d+|DQ-\d+|\b[A-D]\d{3}\b|P\d{2}\b|AIR\d+|\d{1,2}:\d{2}(:\d{2})?", " ", clean)
    clean = re.sub(r"(?<=\d),(?=\d{3}\b)", "", clean)  # séparateur de milliers anglais éventuel
    return [float(n.replace(",", ".")) for n in NUM.findall(clean)]


def _matches(x: float, pool: list[float]) -> bool:
    return any(abs(x - y) <= max(0.051, 0.006 * abs(y)) or round(y) == x or round(y, 1) == x for y in pool)


def split_sentences(text: str) -> list[str]:
    """Coupe après un point suivi de ses citations, quand la phrase suivante commence par une majuscule."""
    text = text.strip()
    out = [m.group(0).strip() for m in
           re.finditer(r".+?[.!?](?:\s*\[[^\]]+\])*(?=\s+[A-ZÉÈÀÂÎÔÛÇ«\"]|\s*$)", text, flags=re.S)]
    return out or ([text] if text else [])


INTERP = re.compile(r"\s*\*?(hypoth[eè]se|hypothesis|interpr[eé]tation|interpretation)\s*:", re.I)


class Controller:
    def check(self, text: str, evidence: dict[str, Evidence], allowed: set[str]) -> list[Sentence]:
        out = []
        for s in split_sentences(text):
            ids = [i.strip() for i in CITE.findall(s)]
            sent = Sentence(s, ids)
            if not ids and INTERP.match(s):
                # une hypothèse annoncée comme telle passe, à condition de ne rien chiffrer
                sent.interpretation = True
                if _numbers(s):
                    sent.ok = False
                    sent.issues.append("hypothesis with figures and no evidence")
                out.append(sent)
                continue
            if not ids:
                sent.ok = False
                sent.issues.append("no citation")
            unknown = [i for i in ids if i not in allowed]
            if unknown:
                sent.ok = False
                sent.issues.append("citation outside the evidence file: " + ", ".join(unknown))
            pool = [n for i in ids if i in evidence for n in _numbers(evidence[i].text + " " + i)]
            bad = [n for n in _numbers(s) if not _matches(n, pool)]
            if bad:
                sent.ok = False
                sent.issues.append("figure missing from the cited evidence: " + ", ".join(_fmt(b, 2) for b in bad))
            if any(evidence[i].simulated for i in ids if i in evidence) and not re.search(r"simul", s, re.I):
                sent.ok = False
                sent.issues.append("relies on simulated evidence without saying so")
            out.append(sent)
        return out


class Assistant:
    def __init__(self, ds: Dataset, log: pd.DataFrame, trace: pd.DataFrame, impact: tuple[dict, dict] | None = None,
                 res: R.Resultats | None = None):
        self.evidence = build_corpus(ds, log, trace)
        self.analyst = Analyst(ds, log, trace, self.evidence, impact, res)
        self.bm25 = BM25({k: v for k, v in self.evidence.items()})
        self.writer = Writer()
        self.controller = Controller()
        names = list(ds.plm["supplier"]) + list(ds.plm["designation"]) + list(ds.mes["operation"])
        self.domain = DOMAIN_STEMS | {stem(t) for n in names for t in raw_tokens(n) if len(t) >= 4}

    def ask(self, question: str) -> Answer:
        q = strip_accents(question.lower())
        steps: list[tuple[str, str]] = []
        a = self.analyst

        if re.search(PERSONAL, q):
            return self._refuse(question, "First names, last names and ages are not loaded by the tool: only ERP "
                                          "employee IDs are. Ask for example for the employee IDs of a station.",
                                ["DQ-09"], steps + [("Analyst", "personal data not loaded")])
        if re.search(PREDICTION, q):
            return self._refuse(question, "The question asks for a forecast. The data covers a single real aircraft "
                                          "over four days: nothing supports extrapolating a delivery date.",
                                ["DQ-09"], steps + [("Analyst", "forecast request refused")])
        if re.search(MISSING_REAL, q):
            return self._refuse(question, "Batches, revisions and defect codes do not exist in the actual data; the "
                                          "ones in the application are simulated.", ["DQ-09"],
                                steps + [("Analyst", "data missing from the sources")])

        facts: list[Fact] = []
        codes = sorted(set(PART_CODE.findall(question.upper())))
        people = re.search(PEOPLE, q) and a.people(question, q)
        if people:
            steps.append(("Analyst", f"ERP employee IDs of the stations concerned: {len(people)} facts"))
            facts += people
        for c in [] if people else codes:
            got = a.part(c)
            steps.append(("Analyst", f"part traceability {c}: {len(got)} facts" if got else f"{c} unknown to the PLM"))
            facts += got
        for p in [] if people else re.findall(STATION, q):
            n = int(p[0] or p[1])
            got = a.station(n)
            steps.append(("Analyst", f"station {n}: {len(got)} facts"))
            facts += got
        if not facts:
            got = a.supplier(question)
            if got:
                steps.append(("Analyst", f"supplier: {len(got)} facts"))
                facts += got
        if not facts:
            got = a.zone(question)
            if got:
                steps.append(("Analyst", f"aircraft zone: {len(got)} facts"))
                facts += got
        generic_skip = {"causes", "conformance", "data_quality", "overview"} if facts else set()
        for name, pattern in [] if people else INTENTS:
            if name in generic_skip:
                continue
            if re.search(pattern, q):
                got = getattr(a, name)()
                steps.append(("Analyst", f"calculation \"{name}\": {len(got)} facts"))
                facts += got

        found = self.bm25.search(question, 8)
        hits = [(i, s) for i, s in found if s > 3.0]
        weak = [(i, s) for i, s in found if s > 1.0]
        steps.append(("Librarian", "BM25: " + (", ".join(f"{i} ({s:.1f})" for i, s in weak) or "nothing relevant")))
        in_scope = bool({stem(t) for t in raw_tokens(question)} & self.domain) or bool(codes)
        if not facts and in_scope:
            # pas de calcul dédié : on donne au rédacteur les preuves proches et le contexte général
            weak = [(i, s) for i, s in weak if s > 2.5]
            facts += a.overview() + a.bottlenecks()[:2] + a.causes()[:1]
            for i, _ in weak[:3]:
                e = self.evidence[i]
                body = re.sub(r"\.\s+(?=\S)", "; ", e.text.strip()).rstrip(".")
                facts.append(Fact(f"Source {i}: {body}.", [i], e.simulated))
            hits = weak
            steps.append(("Analyst", "no dedicated calculation: interpretation mode on nearby evidence"))

        if not facts:
            return self._refuse(question, "This question is outside the tool's scope: no MES, PLM, ERP, manual or 3D "
                                          "model data relates to it.", [], steps)

        seen, uniq = set(), []
        for f in facts:
            if f.claim not in seen:
                uniq.append(f)
                seen.add(f.claim)
        facts = uniq[:10]
        allowed = {i for f in facts for i in f.ids} | {i for i, _ in hits}

        draft = self.writer.write(question, facts, self.evidence)
        err = self.writer.last_error
        steps.append(("Writer", f"LLM unavailable ({err}), falling back to the template" if err else self.writer.mode))
        if draft.strip().upper().startswith(REFUSAL):
            return self._refuse(question, draft.strip()[len(REFUSAL):].strip(" :.-") or "The writer considers the "
                                "facts insufficient.", [], steps)
        sentences = self.controller.check(draft, self.evidence, allowed)
        kept = [s for s in sentences if s.ok]
        steps.append(("Checker", f"{len(kept)}/{len(sentences)} sentences validated"))
        if self.writer.mode != "deterministic" and not err and len(kept) < max(1, len(sentences) // 2):
            draft = self.writer.template(facts)
            sentences = self.controller.check(draft, self.evidence, allowed)
            kept = [s for s in sentences if s.ok]
            steps.append(("Checker", f"LLM answer rejected, falling back to the template: {len(kept)}/{len(sentences)}"))
        if not kept:
            return self._refuse(question, "No sentence passed the evidence check.", [], steps, sentences)
        return Answer(question, " ".join(s.text for s in kept), False, sentences,
                      {i: self.evidence[i] for s in kept for i in s.ids if i in self.evidence}, steps, self.writer.mode)

    def _refuse(self, question, reason, ids, steps, sentences=None) -> Answer:
        text = f"{REFUSAL}. {reason}"
        return Answer(question, text, True, sentences or [], {i: self.evidence[i] for i in ids if i in self.evidence},
                      steps + [("Checker", "refusal")], self.writer.mode)


DEMO_QUESTIONS = [
    "Which operations should be investigated first?",
    "Where are the bottlenecks?",
    "What happened at station 31?",
    "Trace part D145",
    "Why do operations overrun?",
    "How many critical delays?",
    "What do you recommend?",
    "Which parts cost the most?",
    "Who was late on part A623?",
    "Which employee IDs work at station 31?",
    "How many aircraft will we deliver in December?",
    "What is the name of the operator at station 12?",
]
