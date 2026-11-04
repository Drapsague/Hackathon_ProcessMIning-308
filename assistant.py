"""Assistant à preuves : quatre agents, aucune phrase sans source.

  Analyste       lit la question, appelle des calculs déterministes (pandas) qui renvoient des faits
                 déjà chiffrés, chacun avec ses identifiants de preuve ;
  Documentaliste complète par une recherche BM25 dans le corpus (lignes MES, PLM, ERP agrégé,
                 pages de la notice, constats qualité) ;
  Rédacteur      formule la réponse : un LLM si une clé est configurée, sinon un gabarit ;
  Contrôleur     vérifie chaque phrase : au moins une citation, citations présentes dans le dossier
                 de preuves, chiffres présents dans les preuves citées, mention « simulé » si la preuve
                 est simulée. Une phrase qui échoue est retirée ; s'il ne reste rien, on refuse.

Le LLM ne voit que des faits pseudonymisés et ne calcule rien.
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
from etl import FAMILY_LABELS, PART_CODE, Dataset, strip_accents

REFUSAL = "DONNÉES INSUFFISANTES"


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
    s = f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{x:.0f}"
    return s.replace(".", ",")


def build_corpus(ds: Dataset, log: pd.DataFrame, trace: pd.DataFrame) -> dict[str, Evidence]:
    ev: dict[str, Evidence] = {}

    def put(e: Evidence):
        ev[e.id] = e

    for r in ds.mes.itertuples():
        refs = ", ".join(f"{c} x{n}" for c, n in Counter(r.refs).items()) or "aucune"
        put(Evidence(r.evidence, (
            f"Poste {r.poste} « {r.operation} » : prévu {_fmt(r.planned_min)} min, réel {_fmt(r.real_min, 2)} min "
            f"(+{_fmt(r.overrun_min, 2)} min, +{_fmt(r.overrun_pct, 0)} %). Aléa « {r.alea} », cause déclarée "
            f"« {r.cause} », famille {r.family}. Références : {refs}. Début {r.start:%d/%m %H:%M:%S}, fin déclarée "
            f"{r.end_declared:%H:%M:%S}" + (", fin tronquée à 17:00." if r.truncated else ".")), "MES réel"))
    for r in ds.plm.itertuples():
        put(Evidence(r.evidence, (
            f"{r.code} {r.designation} : {r.qty_bom} en nomenclature, fournisseur {r.supplier}, délai "
            f"{r.lead_time}, criticité {r.criticality}, {_fmt(r.mass_kg, 2)} kg, {r.unit_cost_eur:.0f} €, "
            f"temps CAO {r.cad_hours:.0f} h (ligne Excel {r.plm_line})."), "PLM"))
    for poste, g in ds.erp.groupby("poste"):
        levels = ", ".join(f"{k} {v}" for k, v in g["level"].value_counts().items())
        put(Evidence(f"ERP:P{poste}", (
            f"Poste {poste} : {len(g)} opérateurs affectés (pseudonymisés), coût horaire moyen "
            f"{_fmt(g['hourly_cost_eur'].mean(), 2)} €, niveaux : {levels}."), "ERP agrégé"))
    for r in ds.notice_steps.itertuples():
        put(Evidence(r.evidence, (
            f"Notice Meccano page {r.page_notice}, étape {r.etape} : section {r.section}, liste de pièces "
            f"{r.liste_pieces}." + (f" {r.note}." if isinstance(r.note, str) and r.note else "")), "Notice"))
    inv = ds.notice_inv
    put(Evidence("NOTICE:p43", "Inventaire de la notice page 43 : " + ", ".join(
        f"{c or 'planche autocollants'} x{q}" for c, q in zip(inv["code"], inv["quantite"])) + ".", "Notice"))
    for code, n in ds.cad.items():
        put(Evidence(f"CAD:{code}", f"Maquette 3D : la référence {code} apparaît {n} fois dans l'arbre produit.",
                     "CAO"))
    for f in ds.findings:
        put(Evidence(f.id, f"{f.title}. {f.detail}", "Contrôle qualité"))
    from simulate import ECOS
    for e in ECOS:
        put(Evidence(e["eco"], f"{e['eco']} (fictif) : {e['code']} passe en révision {e['rev']} à partir de "
                               f"MSN-{e['from_case']:03d}. {e['motif']}.", "PLM simulé", True))
    return ev


STOP = set("le la les de des du un une et en a au aux pour par sur dans est sont quel quelle quels quelles "
           "qui que quoi comment pourquoi ce cette ces il elle on nous vous ils plus moins avec sans se sa son "
           "ses leur leurs ou ne pas y d l qu c s".split())


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
                 impact: tuple[dict, dict] | None = None):
        self.ds, self.log, self.trace, self.ev = ds, log, trace, evidence
        self.real = log[log["source"] == "réel"]
        self.sim = log[log["source"] == "simulé"]
        self.impact = impact

    def stat(self, sid: str, text: str, simulated: bool = False) -> str:
        self.ev[sid] = Evidence(sid, text, "Calcul simulé" if simulated else "Calcul sur données réelles", simulated)
        return sid

    def overview(self) -> list[Fact]:
        k = M.kpis(self.real)
        sid = self.stat("STAT:cycle_msn001", (
            f"MSN-001 : somme des temps réels {_fmt(k['lead_time_h'])} h, somme des temps prévus "
            f"{_fmt(k['planned_h'])} h, soit +{_fmt((k['lead_time_h'] / k['planned_h'] - 1) * 100, 0)} %. "
            f"Performance (prévu/réel) {_fmt(k['performance'] * 100, 1)} %. Toutes les 56 opérations dépassent."))
        return [Fact(f"Sur MSN-001, l'assemblage a pris {_fmt(k['lead_time_h'])} h réelles pour "
                     f"{_fmt(k['planned_h'])} h prévues, soit +{_fmt((k['lead_time_h'] / k['planned_h'] - 1) * 100, 0)} %, "
                     f"et les 56 opérations dépassent leur temps prévu.", [sid])]

    def bottlenecks(self) -> list[Fact]:
        top = self.ds.mes.sort_values("overrun_min", ascending=False).head(3)
        facts = []
        for i, r in enumerate(top.itertuples()):
            lead = (f"Sur MSN-001, le poste {r.poste} ({r.operation}) perd le plus de temps" if i == 0
                    else f"Vient ensuite le poste {r.poste} ({r.operation})")
            facts.append(Fact(f"{lead} : {_fmt(r.real_min, 2)} min réelles pour {_fmt(r.planned_min)} prévues "
                              f"(+{_fmt(r.overrun_min, 2)} min).", [r.evidence]))
        b = M.bottlenecks(self.sim, self.ds, 2)
        for r in b.itertuples():
            sid = self.stat(f"SIM:queue_P{int(r.poste):02d}", (
                f"Simulation 40 avions : file d'attente cumulée devant le poste {int(r.poste)} "
                f"{_fmt(r.queue_total_min / 60, 0)} h, utilisation {_fmt(r.utilisation * 100, 0)} %."), True)
            facts.append(Fact(f"Dans la flotte simulée, la file d'attente cumulée devant le poste {int(r.poste)} "
                              f"atteint {_fmt(r.queue_total_min / 60, 0)} h.", [sid], True))
        return facts

    def station(self, poste: int) -> list[Fact]:
        m = self.ds.mes.set_index("poste")
        if poste not in m.index:
            return []
        r = m.loc[poste]
        facts = [Fact(f"Le poste {poste} ({r['operation']}) a duré {_fmt(r['real_min'], 2)} min pour "
                      f"{_fmt(r['planned_min'])} prévues, avec l'aléa déclaré « {r['alea']} ».", [r["evidence"]])]
        if r["truncated"]:
            facts.append(Fact(f"Son heure de fin est bloquée à 17:00 alors que la durée déclarée dépasse : "
                              f"on ne sait pas quand le travail s'est réellement terminé.", [r["evidence"], "DQ-01"]))
        step = self.ds.notice_steps.set_index("etape").loc[poste]
        facts.append(Fact(f"Dans la notice, c'est l'étape {poste} page {step['page_notice']}, section "
                          f"{step['section']}.", [step["evidence"]]))
        if f"ERP:P{poste}" in self.ev:
            facts.append(Fact(f"L'ERP affecte {len(self.ds.erp[self.ds.erp['poste'] == poste])} opérateurs "
                              f"pseudonymisés à ce poste.", [f"ERP:P{poste}"]))
        st = M.station_table(self.sim, self.ds).set_index("poste")
        if poste in st.index:
            s = st.loc[poste]
            sid = self.stat(f"SIM:poste_P{poste:02d}", (
                f"Simulation : poste {poste}, {int(s['reworks'])} reprises sur {int(s['passages'])} avions, "
                f"file d'attente cumulée {_fmt(s['queue_total_min'] / 60, 0)} h."), True)
            facts.append(Fact(f"En simulation, ce poste compte {int(s['reworks'])} reprises sur "
                              f"{int(s['passages'])} avions.", [sid], True))
        return facts

    def part(self, code: str) -> list[Fact]:
        if f"PLM:{code}" not in self.ev:
            return []
        p = self.ds.plm.set_index("code").loc[code]
        bom = self.ds.bom.set_index("code").loc[code]
        uses = self.ds.mes[self.ds.mes["refs"].map(lambda r: code in r)]
        facts = [Fact(f"{code} ({p['designation']}) figure {int(p['qty_bom'])} fois dans la nomenclature, "
                      f"fournie par {p['supplier']} sous {p['lead_time']}, criticité {p['criticality']}.",
                      [f"PLM:{code}"])]
        if len(uses):
            sid = self.stat(f"STAT:conso_{code}", (
                f"MSN-001 : {code} apparaît {int(bom['mes'])} fois dans le MES (nomenclature {int(p['qty_bom'])}), "
                f"postes {', '.join(map(str, uses['poste']))} (lignes {', '.join(uses['evidence'])})."))
            facts.append(Fact(f"Le MES la consomme {int(bom['mes'])} fois sur MSN-001, aux postes "
                              f"{', '.join(map(str, uses['poste']))}.", [sid] + uses["evidence"].tolist()[:3]))
        else:
            facts.append(Fact(f"Le MES ne la consomme jamais sur MSN-001.", ["DQ-04"]))
        if f"CAD:{code}" in self.ev:
            facts.append(Fact(f"La maquette 3D la contient {int(self.ds.cad[code])} fois.", [f"CAD:{code}"]))
        for f in self.ds.findings:
            if code in f.detail and f.id in ("DQ-03", "DQ-05", "DQ-06"):
                facts.append(Fact(f"Point d'attention : {f.title}.", [f.id]))
        t = self.trace[(self.trace["code"] == code) & (self.trace["source"] == "simulé")]
        if len(t):
            revs = ", ".join(sorted(t["plm_rev"].unique()))
            sid = self.stat(f"SIM:trace_{code}", (
                f"Simulation : {code} tracée sur {t['case_id'].nunique()} avions, révisions {revs}, "
                f"{t['erp_lot'].nunique()} lots ERP."), True)
            facts.append(Fact(f"En simulation, elle est tracée sur {t['case_id'].nunique()} avions avec "
                              f"{t['erp_lot'].nunique()} lots ERP et les révisions {revs}.", [sid], True))
        return facts

    def causes(self) -> list[Fact]:
        fp = M.family_pareto(self.ds)
        top3 = fp.head(3)
        share = top3["share"].sum() * 100
        sid = self.stat("STAT:familles", "Minutes perdues sur MSN-001 par famille d'aléa : " + "; ".join(
            f"{r.family} ({r.label}) {_fmt(r.overrun_min, 0)} min, {_fmt(r.share * 100, 0)} %, {r.operations} opérations"
            for r in fp.itertuples()) + ".")
        return [
            Fact(f"Trois familles d'aléas concentrent {_fmt(share, 0)} % des minutes perdues sur MSN-001 : "
                 + ", ".join(f"{r.label.lower()} ({_fmt(r.share * 100, 0)} %)" for r in top3.itertuples()) + ".",
                 [sid]),
            Fact("Ces causes sont déclarées, une par opération, et ne sont pas vérifiées : ce sont des pistes, "
                 "pas des causes racines.", ["DQ-08"]),
        ]

    def rework(self) -> list[Fact]:
        k = M.kpis(self.sim)
        sid = self.stat("SIM:reprises", (
            f"Simulation 40 avions : taux de reprise {_fmt(k['rework_rate'] * 100, 0)} % des opérations, "
            f"{_fmt(k['rework_time_share'] * 100, 0)} % du temps de travail."), True)
        return [
            Fact("Les données réelles ne contiennent aucun code défaut ni aucune reprise : le taux de reprise "
                 "réel n'est pas mesurable.", ["DQ-09"]),
            Fact(f"Dans la simulation, calibrée sur les 25 % annoncés par le brief, {_fmt(k['rework_rate'] * 100, 0)} % "
                 f"des opérations sont reprises, soit {_fmt(k['rework_time_share'] * 100, 0)} % du temps de travail.",
                 [sid], True),
        ]

    def oee(self) -> list[Fact]:
        kr, ks = M.kpis(self.real), M.kpis(self.sim)
        sid_r = self.stat("STAT:performance", f"MSN-001 : performance (temps prévu / temps réel) "
                                              f"{_fmt(kr['performance'] * 100, 1)} %.")
        sid_s = self.stat("SIM:trs", (
            f"Simulation : TRS {_fmt(ks['oee'] * 100, 1)} % = disponibilité {_fmt(ks['availability'] * 100, 1)} % x "
            f"performance {_fmt(ks['performance'] * 100, 1)} % x qualité {_fmt(ks['quality'] * 100, 1)} %."), True)
        return [
            Fact(f"Sur MSN-001, seule la performance est mesurable : {_fmt(kr['performance'] * 100, 1)} %.", [sid_r]),
            Fact("La qualité et la disponibilité réelles manquent, faute de codes défaut et d'arrêts tracés.",
                 ["DQ-09"]),
            Fact(f"Le TRS simulé vaut {_fmt(ks['oee'] * 100, 1)} %.", [sid_s], True),
        ]

    def wip(self) -> list[Fact]:
        k = M.kpis(self.sim)
        planned_max = self.ds.mes["planned_min"].max()
        real_max = self.ds.mes["real_min"].max()
        sid = self.stat("SIM:encours", (
            f"Simulation : lancement toutes les {_fmt(planned_max, 0)} min (temps prévu du poste le plus long), "
            f"encours moyen {_fmt(k['wip_mean'], 1)} avions, temps de cycle du premier avion "
            f"{_fmt(k['lead_time_first_h'], 1)} h, du dernier {_fmt(k['lead_time_last_h'], 1)} h."), True)
        return [
            Fact(f"Le poste le plus long est prévu à {_fmt(planned_max, 0)} min mais dure {_fmt(real_max, 2)} min "
                 f"sur MSN-001.", ["MES:L32"]),
            Fact(f"En simulation, lancer un avion toutes les {_fmt(planned_max, 0)} min fait croître le temps de cycle "
                 f"de {_fmt(k['lead_time_first_h'], 1)} h pour le premier avion à {_fmt(k['lead_time_last_h'], 1)} h "
                 f"pour le dernier, avec {_fmt(k['wip_mean'], 1)} avions en cours en moyenne.", [sid], True),
        ]

    def data_quality(self) -> list[Fact]:
        sev = [f for f in self.ds.findings if f.severity == "haute"]
        return [Fact(f"{f.title} : {f.detail.split('.')[0]}.", [f.id]) for f in sev[:5]]

    def conformance(self) -> list[Fact]:
        cf = M.conformance(self.real, self.ds).iloc[0]
        sid = self.stat("STAT:conformite_msn001", (
            f"MSN-001 : fitness d'ordre {_fmt(cf['fitness_ordre'] * 100, 0)} % contre la gamme MES, ratio temps "
            f"réel / prévu {_fmt(cf['temps_reel_vs_prevu'], 2)}."))
        return [
            Fact(f"MSN-001 suit exactement l'ordre de la gamme : fitness {_fmt(cf['fitness_ordre'] * 100, 0)} %.", [sid]),
            Fact("La notice compte aussi 56 étapes, dans le même ordre de sections que le MES.",
                 ["NOTICE:p6-e1", "NOTICE:p42-e56"]),
            Fact("L'écart n'est donc pas dans la séquence mais dans les temps et dans la matière.",
                 [sid, "DQ-03"]),
            Fact("Le poste 51 sans pièce correspond à l'étape 51 de la notice, qui n'a pas de liste de pièces.",
                 ["DQ-02"]),
        ]

    def cost(self) -> list[Fact]:
        c = M.overrun_cost(self.ds)
        sid = self.stat("STAT:cout_derive", (
            f"MSN-001 : coût main-d'œuvre de la dérive {_fmt(c['overrun_cost_eur'].sum(), 0)} €, en supposant un "
            f"opérateur par opération et le coût horaire moyen ERP du poste."))
        return [Fact(f"Le coût main-d'œuvre direct de la dérive sur MSN-001 est de {_fmt(c['overrun_cost_eur'].sum(), 0)} €, "
                     f"avec l'hypothèse d'un opérateur par opération.", [sid])]

    def suppliers(self) -> list[Fact]:
        p = self.ds.plm
        crit = p[(p["criticality"] == "Critique")].sort_values("lead_max_days", ascending=False).head(4)
        return [Fact(f"{r.code} ({r.designation}) est critique avec un délai de {r.lead_time} chez {r.supplier}.",
                     [r.evidence]) for r in crit.itertuples()]

    def recommendations(self) -> list[Fact]:
        m = self.ds.mes.set_index("poste")
        p31 = m.loc[31]
        facts = [
            Fact(f"Traiter d'abord le poste 31 : {_fmt(p31['real_min'], 2)} min pour {_fmt(p31['planned_min'])} prévues, "
                 f"aléa « {p31['alea']} ».", [p31["evidence"]]),
            Fact("Interdire de démarrer une opération qui ne peut pas finir avant 17:00, ou tracer sa reprise "
                 "le lendemain, pour ne plus perdre de temps déclaré.", ["DQ-01"]),
            Fact("Corriger la référence de la queue arrière dans le MES et la maquette 3D.", ["DQ-05"]),
            Fact("Recaler le lancement sur le temps réel du goulot et non sur le temps prévu.", ["MES:L32"]),
        ]
        fp = M.family_pareto(self.ds).head(3)
        sid = self.stat("STAT:familles_top3", "Familles d'aléas les plus coûteuses sur MSN-001 : " + ", ".join(
            f"{r.label} {_fmt(r.share * 100, 0)} %" for r in fp.itertuples()) + ".")
        facts.append(Fact("Cibler la maintenance et l'environnement atelier, familles qui pèsent le plus dans les "
                          "minutes perdues.", [sid, "DQ-08"]))
        if self.impact:
            a, b = self.impact
            sid = self.stat("SIM:impact", (
                f"Simulation du plan d'action sur {a['cases']} avions : temps de cycle {_fmt(a['lead_time_h'], 1)} h -> {_fmt(b['lead_time_h'], 1)} h, "
                f"reprise {_fmt(a['rework_rate'] * 100, 0)} % -> {_fmt(b['rework_rate'] * 100, 0)} %, "
                f"TRS {_fmt(a['oee'] * 100, 1)} % -> {_fmt(b['oee'] * 100, 1)} %."), True)
            facts.append(Fact(f"Simulé sur {a['cases']} avions, ce plan ramène le temps de cycle de {_fmt(a['lead_time_h'], 1)} h à "
                              f"{_fmt(b['lead_time_h'], 1)} h et le TRS de {_fmt(a['oee'] * 100, 1)} % à "
                              f"{_fmt(b['oee'] * 100, 1)} %.", [sid], True))
        return facts


INTENTS = [
    ("recommendations", r"recommand|que faire|plan d.?action|ameliorer|priorit|levier|conseil"),
    ("bottlenecks", r"goulot|bottleneck|retard|plus long|perd|lent|bloqu"),
    ("rework", r"reprise|rework|defaut|non.?conform|rebut"),
    ("oee", r"\btrs\b|\boee\b|rendement"),
    ("wip", r"encours|en.?cours|\bwip\b|takt|cadence|stock"),
    ("causes", r"cause|pourquoi|alea|famille|origine"),
    ("conformance", r"conform|notice|document|gamme|ecart.*process|sequence|ordre|variante"),
    ("data_quality", r"qualite des donnees|incoheren|anomal|erreur|fiab|donnees"),
    ("cost", r"cout|euro|€|budget|argent"),
    ("suppliers", r"fournisseur|supplier|approvisionnement|delai"),
    ("overview", r"temps de cycle|cycle|lead.?time|duree|resume|synthese|combien de temps"),
]

PERSONAL = r"\bnom\b|prenom|\bage\b|\bqui est\b|identit|adresse|telephone|salaire de|matricule|qui a (fait|travaille)"
PREDICTION = r"prevo|predi|futur|l.?annee prochaine|en 202[7-9]|dans \d+ (mois|ans)|combien .* livr.* (decembre|janvier|mois)"
OPERATOR = r"operateur|technicien|ouvrier|personne|equipe .*(lent|faute|responsable)"
MISSING_REAL = r"lot reel|vrai lot|revision reelle|code defaut reel|cause racine"


class Writer:
    """Rédacteur : gabarit déterministe ou LLM. Configuration par variables d'environnement :
    AIRPLUS_LLM=anthropic|groq|openai, ANTHROPIC_API_KEY / GROQ_API_KEY / OPENAI_API_KEY, AIRPLUS_LLM_MODEL,
    OPENAI_BASE_URL pour un serveur compatible (Mistral, Ollama, vLLM...)."""

    SYSTEM = (
        "Tu es l'agent rédacteur d'un outil de process mining pour un industriel aéronautique. Règles strictes :\n"
        "1. Tu n'utilises que les faits fournis. Aucune connaissance extérieure.\n"
        "2. Chaque phrase se termine par une ou plusieurs citations entre crochets, copiées depuis les faits, "
        "par exemple [MES:L32] ou [DQ-01].\n"
        "3. Tu n'écris aucun chiffre qui n'apparaît pas dans les faits cités par la phrase.\n"
        "4. Si un fait est marqué SIMULÉ, la phrase qui l'utilise contient le mot « simulé » ou « simulation ».\n"
        "5. Si les faits ne permettent pas de répondre, réponds exactement « DONNÉES INSUFFISANTES » puis une "
        "phrase qui dit quelle donnée manque.\n"
        "Réponds en français, en 2 à 6 phrases courtes, sans liste ni titre."
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
        return f"LLM ({self.provider})" if self.provider in ("anthropic", "groq", "openai") else "déterministe"

    def template(self, facts: list[Fact]) -> str:
        return " ".join(f"{f.claim} " + "".join(f"[{i}]" for i in f.ids) for f in facts)

    def write(self, question: str, facts: list[Fact], evidence: dict[str, Evidence]) -> str:
        self.last_error = None
        if self.mode == "déterministe":
            return self.template(facts)
        ids = sorted({i for f in facts for i in f.ids})
        lines = [f"- {f.claim} " + "".join(f"[{i}]" for i in f.ids) + (" (SIMULÉ)" if f.simulated else "")
                 for f in facts]
        lines += [f"- Preuve [{i}] : {evidence[i].text}" + (" (SIMULÉ)" if evidence[i].simulated else "")
                  for i in ids if i in evidence]
        user = f"Question : {question}\n\nFaits disponibles :\n" + "\n".join(lines)
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
    clean = re.sub(r"MSN-\d+|LOT-\S+|ECO-\d+|DQ-\d+|\b[A-D]\d{3}\b|P\d{2}\b|\d{1,2}:\d{2}(:\d{2})?", " ", clean)
    return [float(n.replace(",", ".")) for n in NUM.findall(clean)]


def _matches(x: float, pool: list[float]) -> bool:
    return any(abs(x - y) <= max(0.051, 0.006 * abs(y)) or round(y) == x or round(y, 1) == x for y in pool)


def split_sentences(text: str) -> list[str]:
    """Coupe après un point suivi de ses citations, quand la phrase suivante commence par une majuscule."""
    text = text.strip()
    out = [m.group(0).strip() for m in
           re.finditer(r".+?[.!?](?:\s*\[[^\]]+\])*(?=\s+[A-ZÉÈÀÂÎÔÛÇ«]|\s*$)", text, flags=re.S)]
    return out or ([text] if text else [])


class Controller:
    def check(self, text: str, evidence: dict[str, Evidence], allowed: set[str]) -> list[Sentence]:
        out = []
        for s in split_sentences(text):
            ids = [i.strip() for i in CITE.findall(s)]
            sent = Sentence(s, ids)
            if not ids:
                sent.ok = False
                sent.issues.append("aucune citation")
            unknown = [i for i in ids if i not in allowed]
            if unknown:
                sent.ok = False
                sent.issues.append("citation hors dossier : " + ", ".join(unknown))
            pool = [n for i in ids if i in evidence for n in _numbers(evidence[i].text + " " + i)]
            bad = [n for n in _numbers(s) if not _matches(n, pool)]
            if bad:
                sent.ok = False
                sent.issues.append("chiffre absent des preuves citées : " + ", ".join(_fmt(b, 2) for b in bad))
            if any(evidence[i].simulated for i in ids if i in evidence) and not re.search(r"simul", s, re.I):
                sent.ok = False
                sent.issues.append("s'appuie sur une preuve simulée sans le dire")
            out.append(sent)
        return out


class Assistant:
    def __init__(self, ds: Dataset, log: pd.DataFrame, trace: pd.DataFrame, impact: tuple[dict, dict] | None = None):
        self.evidence = build_corpus(ds, log, trace)
        self.analyst = Analyst(ds, log, trace, self.evidence, impact)
        self.bm25 = BM25({k: v for k, v in self.evidence.items()})
        self.writer = Writer()
        self.controller = Controller()

    def ask(self, question: str) -> Answer:
        q = strip_accents(question.lower())
        steps: list[tuple[str, str]] = []
        a = self.analyst

        if re.search(PERSONAL, q):
            return self._refuse(question, "La question porte sur des données personnelles. L'ERP est pseudonymisé à "
                                          "la lecture et l'assistant n'a accès qu'à des agrégats par poste.",
                                ["DQ-09"], steps + [("Analyste", "garde-fou RGPD déclenché")])
        if re.search(PREDICTION, q):
            return self._refuse(question, "La question demande une prévision. Les données couvrent un seul avion "
                                          "réel sur quatre jours : rien ne permet d'extrapoler une date de livraison.",
                                ["DQ-09"], steps + [("Analyste", "demande de prévision refusée")])
        if re.search(OPERATOR, q) and not re.search(r"cout|affect|combien d", q):
            return self._refuse(question, "Le MES ne dit pas qui a réalisé chaque opération. Dans la simulation, les "
                                          "opérateurs sont affectés à tour de rôle : on ne peut pas juger une personne "
                                          "sur cette base.", ["DQ-09"],
                                steps + [("Analyste", "question sur des personnes : ressource non tracée")])
        if re.search(MISSING_REAL, q):
            return self._refuse(question, "Lots, révisions et codes défaut n'existent pas dans les données réelles ; "
                                          "ceux de l'application sont simulés.", ["DQ-09"],
                                steps + [("Analyste", "donnée absente des sources")])

        facts: list[Fact] = []
        codes = sorted(set(PART_CODE.findall(question.upper())))
        for c in codes:
            got = a.part(c)
            steps.append(("Analyste", f"traçabilité pièce {c} : {len(got)} faits" if got else f"{c} inconnue du PLM"))
            facts += got
        for p in re.findall(r"poste\s*(\d{1,2})|\bp(\d{2})\b", q):
            n = int(p[0] or p[1])
            got = a.station(n)
            steps.append(("Analyste", f"poste {n} : {len(got)} faits"))
            facts += got
        generic_skip = {"causes", "conformance", "data_quality", "overview"} if facts else set()
        for name, pattern in INTENTS:
            if name in generic_skip:
                continue
            if re.search(pattern, q):
                got = getattr(a, name)()
                steps.append(("Analyste", f"calcul « {name} » : {len(got)} faits"))
                facts += got

        hits = [(i, s) for i, s in self.bm25.search(question, 6) if s > 3.0]
        steps.append(("Documentaliste", "BM25 : " + (", ".join(f"{i} ({s:.1f})" for i, s in hits) or "rien au-dessus du seuil")))
        if not facts and hits:
            for i, _ in hits[:3]:
                e = self.evidence[i]
                facts.append(Fact(f"Source {i} : {e.text}", [i], e.simulated))

        if not facts:
            return self._refuse(question, "Aucune donnée des trois systèmes ni de la notice ne permet de répondre.",
                                [], steps)

        seen, uniq = set(), []
        for f in facts:
            if f.claim not in seen:
                uniq.append(f)
                seen.add(f.claim)
        facts = uniq[:8]
        allowed = {i for f in facts for i in f.ids} | {i for i, _ in hits}

        draft = self.writer.write(question, facts, self.evidence)
        err = self.writer.last_error
        steps.append(("Rédacteur", f"LLM indisponible ({err}), repli sur le gabarit" if err else self.writer.mode))
        if draft.strip().upper().startswith(REFUSAL):
            return self._refuse(question, draft.strip()[len(REFUSAL):].strip(" :.-") or "Le rédacteur estime les faits "
                                "insuffisants.", [], steps)
        sentences = self.controller.check(draft, self.evidence, allowed)
        kept = [s for s in sentences if s.ok]
        steps.append(("Contrôleur", f"{len(kept)}/{len(sentences)} phrases validées"))
        if self.writer.mode != "déterministe" and not err and len(kept) < max(1, len(sentences) // 2):
            draft = self.writer.template(facts)
            sentences = self.controller.check(draft, self.evidence, allowed)
            kept = [s for s in sentences if s.ok]
            steps.append(("Contrôleur", f"réponse LLM rejetée, repli sur le gabarit : {len(kept)}/{len(sentences)}"))
        if not kept:
            return self._refuse(question, "Aucune phrase n'a passé le contrôle des preuves.", [], steps, sentences)
        return Answer(question, " ".join(s.text for s in kept), False, sentences,
                      {i: self.evidence[i] for s in kept for i in s.ids if i in self.evidence}, steps, self.writer.mode)

    def _refuse(self, question, reason, ids, steps, sentences=None) -> Answer:
        text = f"{REFUSAL}. {reason}"
        return Answer(question, text, True, sentences or [], {i: self.evidence[i] for i in ids if i in self.evidence},
                      steps + [("Contrôleur", "refus")], self.writer.mode)


DEMO_QUESTIONS = [
    "Où sont les goulots ?",
    "Que s'est-il passé au poste 31 ?",
    "Trace la pièce D145",
    "Pourquoi les opérations dérivent-elles ?",
    "Quel est le TRS ?",
    "Que recommandes-tu ?",
    "Quel opérateur est le plus lent ?",
    "Combien d'avions livrerons-nous en décembre ?",
    "Quel est le nom de l'opérateur du poste 12 ?",
]
