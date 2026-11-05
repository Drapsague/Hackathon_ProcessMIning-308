"""Exports PDF des pages du dashboard et du résumé des anomalies (fpdf2 + graphiques matplotlib), en anglais."""
from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from fpdf import FPDF
from fpdf.fonts import FontFace

import rules as R
from etl import FAMILY_LABELS, Dataset

NAVY, BLUE, GREY = "#1E3A8A", "#2563EB", "#94A3B8"

# DejaVu Sans est livrée avec matplotlib : elle couvre €, ≥, «», etc. (les polices de base du PDF, non)
_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
FONT = "DejaVu"
LOGO = Path(__file__).parent / "assets" / "scout_logo.png"


def _t(text) -> str:
    return str(text)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def eur(x: float) -> str:
    return f"€{x:,.0f}"


class _Pdf(FPDF):
    titre_page = ""

    def header(self):
        self.set_font(FONT, "B", 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 6, _t(f"SCOUT · Air+ | Industrial Performance Radar - {self.titre_page}"), align="L")
        self.ln(8)

    def footer(self):
        self.set_y(-12)
        self.set_font(FONT, "", 8)
        self.set_text_color(100, 116, 139)
        self.cell(0, 6, _t(f"Business rules v1.2 · actual MES, PLM, ERP data · page {self.page_no()}"), align="C")


def _nouveau(titre: str, sous_titre: str = "") -> _Pdf:
    pdf = _Pdf(format="A4")
    pdf.add_font(FONT, "", str(_FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font(FONT, "B", str(_FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.titre_page = titre
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    if LOGO.exists():
        pdf.image(str(LOGO), x=pdf.w - pdf.r_margin - 38, y=7, w=38)
    pdf.set_font(FONT, "B", 16)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 9, _t(titre), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT, "", 9)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 5, _t(f"Generated on {dt.datetime.now():%d/%m/%Y at %H:%M}" + (f" · {sous_titre}" if sous_titre else "")),
             new_x="LMARGIN", new_y="NEXT")
    return pdf


def _titre(pdf: FPDF, text: str):
    pdf.ln(3)
    pdf.set_font(FONT, "B", 12)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(31, 41, 55)
    pdf.set_font(FONT, "", 9)


def _texte(pdf: FPDF, text: str, size: float = 8.5):
    pdf.set_font(FONT, "", size)
    pdf.multi_cell(0, 4.6, _t(text), new_x="LMARGIN", new_y="NEXT")


def _table(pdf: FPDF, rows: list[list], widths: tuple, colorer: dict[int, dict] | None = None, size: float = 8.5):
    pdf.set_font(FONT, "", size)
    head = FontFace(emphasis="BOLD", fill_color=(226, 232, 240), color=(15, 23, 42))
    neutre = FontFace(emphasis="", fill_color=(255, 255, 255), color=(31, 41, 55))
    with pdf.table(col_widths=widths, headings_style=head, line_height=size * 0.6, text_align="LEFT",
                   borders_layout="HORIZONTAL_LINES") as table:
        for i, r in enumerate(rows):
            row = table.row()
            for j, v in enumerate(r):
                style = head if i == 0 else neutre
                if i and colorer and j in colorer and str(v) in colorer[j]:
                    c = colorer[j][str(v)]
                    ink = (31, 41, 55) if c in ("#EAB308", "#94A3B8") else (255, 255, 255)
                    style = FontFace(emphasis="BOLD", color=ink, fill_color=_rgb(c))
                row.cell(_t(v), style=style)


def _kpis(pdf: FPDF, items: list[tuple[str, str, str, str]]):
    """Cartes KPI : (libellé, valeur, précision, couleur) sur une ligne."""
    n = len(items)
    w = (pdf.w - pdf.l_margin - pdf.r_margin - 3 * (n - 1)) / n
    y = pdf.get_y() + 1
    for i, (lab, val, sub, col) in enumerate(items):
        x = pdf.l_margin + i * (w + 3)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_draw_color(226, 232, 240)
        pdf.rect(x, y, w, 22, style="DF")
        pdf.set_fill_color(*_rgb(col))
        pdf.rect(x, y, 1.6, 22, style="F")
        pdf.set_xy(x + 4, y + 2)
        pdf.set_font(FONT, "", 7.5)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(w - 6, 4, _t(lab))
        pdf.set_xy(x + 4, y + 7)
        pdf.set_font(FONT, "B", 14 if len(val) < 12 else 11)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(w - 6, 7, _t(val))
        pdf.set_xy(x + 4, y + 15)
        pdf.set_font(FONT, "", 7)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(w - 6, 4, _t(sub))
    pdf.set_text_color(31, 41, 55)
    pdf.set_y(y + 25)


def _figure(pdf: FPDF, fig, w: float | None = None, x: float | None = None, y: float | None = None):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    kw = {"w": w or (pdf.w - pdf.l_margin - pdf.r_margin)}
    if x is not None:
        kw["x"] = x
    if y is not None:
        kw["y"] = y
    pdf.image(buf, **kw)


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color("#CBD5E1")
    ax.spines["bottom"].set_color("#CBD5E1")
    ax.tick_params(colors="#475569", labelsize=8)
    ax.grid(axis="x" if ax.get_xlim() else "y", color="#E2E8F0", linewidth=0.6)
    ax.set_axisbelow(True)


# Graphiques partagés avec l'application (mêmes calculs, mêmes couleurs)

def fig_top5(res: R.Resultats):
    d = R.depassement_par_poste(res).head(5).iloc[::-1]
    fig, ax = plt.subplots(figsize=(5.2, 2.6))
    bars = ax.barh([f"Station {p}" for p in d["poste"]], d["dep"], color=BLUE)
    ax.bar_label(bars, labels=[f"{v:.1f}" for v in d["dep"]], padding=3, fontsize=8, color="#1F2937")
    ax.set_xlabel("Overrun (minutes)", fontsize=8, color="#475569")
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.set_title("Duration overrun by station (MES) - top 5", fontsize=9.5, loc="left", color="#0F172A")
    return fig


def fig_donut(res: R.Resultats):
    o = res.operations
    counts = o["priorite"].value_counts().reindex(R.PRIORITES, fill_value=0)
    prov = o[o["score_provisoire"]]["priorite"].value_counts()
    fig, ax = plt.subplots(figsize=(5.2, 2.6))
    nz = counts[counts > 0]
    ax.pie(nz, colors=[R.COULEURS_PRIORITE[k] for k in nz.index], startangle=90, counterclock=False,
           wedgeprops=dict(width=0.32, edgecolor="white"))
    ax.text(0, 0.08, str(len(o)), ha="center", va="center", fontsize=16, fontweight="bold", color="#0F172A")
    ax.text(0, -0.2, "operations", ha="center", va="center", fontsize=8, color="#475569")
    labels = {"Low": "Low priority (0-29)", "Medium": "Medium priority (30-59)", "High": "High priority (60-100)"}
    handles = [plt.Rectangle((0, 0), 1, 1, color=R.COULEURS_PRIORITE[k]) for k in R.PRIORITES]
    txt = [f"{labels[k]}: {counts[k]} ({counts[k] / len(o) * 100:.1f}%)"
           + (f", incl. {prov[k]} provisional" if prov.get(k, 0) else "") for k in R.PRIORITES]
    ax.legend(handles, txt, loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=7.5)
    ax.set_title("Operations by priority level (P04)", fontsize=9.5, loc="left", color="#0F172A")
    return fig


def fig_gravite(res: R.Resultats):
    g = res.operations["gravite"].value_counts().reindex(R.GRAVITES, fill_value=0)
    fig, ax = plt.subplots(figsize=(5.2, 2.4))
    bars = ax.bar(g.index, g.values, color=[R.COULEURS_GRAVITE[k] for k in g.index])
    ax.bar_label(bars, fontsize=8)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.set_title("Delays by severity", fontsize=9.5, loc="left", color="#0F172A")
    return fig


def fig_retard_poste(ops: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(10.5, 2.6))
    ax.bar(ops["poste"].astype(str), ops["retard_pct"], color=[R.COULEURS_GRAVITE[g] for g in ops["gravite"]])
    for y, lab in ((20, "major threshold"), (50, "critical threshold")):
        ax.axhline(y, color="#64748B", linestyle="--", linewidth=0.8)
        ax.text(len(ops) - 0.5, y + 1, lab, fontsize=7, color="#64748B", ha="right")
    ax.set_ylabel("delay (%)", fontsize=8, color="#475569")
    ax.tick_params(axis="x", labelsize=6)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.set_title("Delay by station (routing order)", fontsize=9.5, loc="left", color="#0F172A")
    return fig


def fig_delais(res: R.Resultats):
    p = res.pieces.sort_values("delai_jours", ascending=False)
    fig, ax = plt.subplots(figsize=(10.5, 2.8))
    ax.bar(p["code"], p["delai_jours"], color=[R.COULEURS_CRITICITE.get(c, GREY) for c in p["criticality"]])
    ax.axhline(res.q3_delai, color="#DC2626", linestyle="--", linewidth=0.9)
    ax.text(len(p) - 0.5, res.q3_delai + 1, f"Q3 = {res.q3_delai:.2f} d", fontsize=7,
            color="#DC2626", ha="right")
    ax.set_ylabel("lead time used (days)", fontsize=8, color="#475569")
    ax.tick_params(axis="x", labelsize=6, rotation=90)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in R.COULEURS_CRITICITE.values()]
    ax.legend(handles, list(R.COULEURS_CRITICITE), fontsize=7, frameon=False, ncol=4, loc="upper right")
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.set_title("Supply lead time by reference, colour = criticality", fontsize=9.5, loc="left",
                 color="#0F172A")
    return fig


def fig_effectifs(res: R.Resultats):
    po = res.postes
    fig, ax = plt.subplots(figsize=(10.5, 2.4))
    ax.bar(po["poste"].astype(str), po["effectif"], color=BLUE)
    ax.set_ylabel("employee IDs", fontsize=8, color="#475569")
    ax.tick_params(axis="x", labelsize=6)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.set_title("Declared ERP headcount by station", fontsize=9.5, loc="left", color="#0F172A")
    return fig


def fig_pareto(res: R.Resultats, n: int = 15):
    v = res.fin_refs.head(n)
    fig, ax = plt.subplots(figsize=(10.5, 3.0))
    ax.bar(v["code"], v["cout_reference"] / 1e6, color=[R.COULEURS_CRITICITE.get(c, GREY) for c in v["criticality"]])
    ax.set_ylabel("cost (€M)", fontsize=8, color="#475569")
    ax2 = ax.twinx()
    ax2.plot(v["code"], v["part_cumulee_pct"], color=NAVY, marker="o", markersize=3, linewidth=1.4)
    ax2.axhline(80, color="#64748B", linestyle="--", linewidth=0.8)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("cumulative share (%)", fontsize=8, color=NAVY)
    ax2.tick_params(labelsize=8, colors=NAVY)
    for s in ("top",):
        ax2.spines[s].set_visible(False)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.tick_params(axis="x", labelsize=7, rotation=45)
    ax.set_title(f"Cost by reference (FIN02) and cumulative share (FIN03), top {n}", fontsize=9.5, loc="left",
                 color="#0F172A")
    return fig


def fig_fin04(res: R.Resultats):
    fp = res.fin_postes
    fig, ax = plt.subplots(figsize=(10.5, 2.4))
    ax.bar(fp["poste"].astype(str), fp["cout_horaire_theorique"], color=BLUE)
    ax.axhline(fp["cout_horaire_theorique"].mean(), color="#64748B", linestyle="--", linewidth=0.8)
    ax.set_ylabel("€ / hour", fontsize=8, color="#475569")
    ax.tick_params(axis="x", labelsize=6)
    _style(ax)
    ax.grid(axis="x", visible=False)
    ax.set_title("Theoretical hourly cost by station (FIN04)", fontsize=9.5, loc="left", color="#0F172A")
    return fig


# Pages

def _bloc_anomalies(pdf: _Pdf, ds: Dataset, res: R.Resultats):
    ops = res.operations
    _titre(pdf, "Investigation signals (cross-checks, never causes)")
    _table(pdf, [
        ["Rule", "Signal", "Operations"],
        ["CROSS01", "Delay + critical part", int(ops["CROSS01"].sum())],
        ["CROSS02", f"Delay + part with lead time > Q3 ({res.q3_delai:.2f} d)", int(ops["CROSS02"].sum())],
        ["CROSS03", "Delay + declared incident", int(ops["CROSS03"].sum())],
        ["CROSS04", "Delay + ERP resources on the station", int(ops["CROSS04"].sum())],
    ], (25, 115, 30))
    _titre(pdf, "Anomalies detected by the rules")
    if res.anomalies:
        _table(pdf, [["Rule", "Object", "Detail", "Evidence"]] + [[a.regle, a.objet, a.detail, a.source]
                                                               for a in res.anomalies], (22, 22, 110, 26), size=8)
    else:
        _texte(pdf, "None.")
    _titre(pdf, "Cross-source consistency checks")
    for f in ds.findings:
        if f.severity not in ("haute", "moyenne"):
            continue
        pdf.set_font(FONT, "B", 8.5)
        pdf.multi_cell(0, 4.6, _t(f"{f.id} · {f.title}"), new_x="LMARGIN", new_y="NEXT")
        _texte(pdf, f.detail)
        pdf.ln(1.5)


def _top_ops(pdf: _Pdf, res: R.Resultats, n: int = 15):
    top = res.operations.sort_values(["score", "retard_pct"], ascending=False).head(n)
    _table(pdf, [["Station", "Operation", "Delay", "Severity", "Declared incident", "Score", "Priority"]] + [
        [r.poste, r.operation, f"{r.retard_pct:.1f}%", r.gravite, r.alea, r.score, r.priorite] for r in top.itertuples()],
        (14, 46, 14, 18, 48, 11, 16), {3: R.COULEURS_GRAVITE, 6: R.COULEURS_PRIORITE}, size=8)


def pdf_vue_globale(ds: Dataset, res: R.Resultats) -> bytes:
    k, o = res.kpi, res.operations
    pdf = _nouveau("Overview", "key indicators")
    n_ret = int((o["ecart_min"] > 0).sum())
    prov_el = int((o["score_provisoire"] & (o["score"] >= 60)).sum())
    _kpis(pdf, [
        ("Delayed operations", f"{n_ret}", f"out of {len(o)} ({k['taux_retard']:.0f}%)", "#DC2626"),
        ("Cumulative overrun", f"{k['retard_cumule_min']:.1f} min", f"{k['retard_cumule_min'] / 60:.1f} h", BLUE),
        ("High-priority operations", f"{k['priorites_elevees']}",
         f"P04 score ≥ 60, {prov_el} provisional", "#7C3AED"),
        ("Total parts cost (FIN01)", eur(k["cout_total_pieces"]), "Σ quantity × PLM cost", "#16A34A"),
    ])
    y = pdf.get_y() + 2
    half = (pdf.w - pdf.l_margin - pdf.r_margin - 4) / 2
    _figure(pdf, fig_top5(res), w=half, x=pdf.l_margin, y=y)
    _figure(pdf, fig_donut(res), w=half, x=pdf.l_margin + half + 4, y=y)
    pdf.set_y(y + 58)
    _texte(pdf, f"Provisional scores: {k['scores_provisoires']} (insufficient data; missing data is not zero "
                "risk).")
    _titre(pdf, "Operations to investigate first")
    _top_ops(pdf, res, 10)
    return bytes(pdf.output())


def pdf_production(ds: Dataset, res: R.Resultats, ops: pd.DataFrame | None = None, filtres: str = "") -> bytes:
    k = res.kpi
    o = res.operations if ops is None else ops
    pdf = _nouveau("MES production analysis", filtres or "all operations")
    h, mn = divmod(round(k["retard_cumule_min"]), 60)
    _kpis(pdf, [("Operations", f"{k['operations']}", "valid MES rows", NAVY),
                ("Delay rate", f"{k['taux_retard']:.0f}%", "delay > 0", "#DC2626"),
                ("Major delays", f"{k['retards_importants']}", "≥ 20% and < 50%", "#F97316"),
                ("Critical delays", f"{k['retards_critiques']}", "≥ 50%", "#DC2626")])
    _kpis(pdf, [("Cumulative delay", f"{k['retard_cumule_min']:.1f} min", f"{h} h {mn:02d}", "#F97316"),
                ("Declared incidents", f"{k['incidents']}", "incident recorded", "#EAB308"),
                ("High priorities", f"{k['priorites_elevees']}", "score ≥ 60", "#DC2626"),
                ("Provisional scores", f"{k['scores_provisoires']}", "insufficient data", GREY)])
    y = pdf.get_y() + 1
    half = (pdf.w - pdf.l_margin - pdf.r_margin - 4) / 2
    _figure(pdf, fig_gravite(res), w=half, x=pdf.l_margin, y=y)
    _figure(pdf, fig_donut(res), w=half, x=pdf.l_margin + half + 4, y=y)
    pdf.set_y(y + 54)
    if len(o):
        _figure(pdf, fig_retard_poste(o))
    _titre(pdf, f"Operations ({len(o)})")
    _table(pdf, [["Station", "Operation", "Date", "Planned", "Actual", "Gap", "Delay", "Severity", "Priority"]] + [
        [r.poste, r.operation, r.start.strftime("%d/%m"), f"{r.planned_min:.0f}", f"{r.real_min:.2f}",
         f"{r.ecart_min:+.2f}", f"{r.retard_pct:.1f}%", r.gravite, r.priorite] for r in o.itertuples()],
        (14, 52, 12, 13, 13, 14, 15, 20, 17), {7: R.COULEURS_GRAVITE, 8: R.COULEURS_PRIORITE}, size=7.5)
    inc = o[o["incident"]].merge(ds.mes[["poste", "family"]], on="poste")
    _titre(pdf, f"Declared industrial incidents ({len(inc)})")
    _table(pdf, [["Station", "Declared incident", "Declared potential cause", "Family"]] + [
        [r.poste, r.alea, r.cause, FAMILY_LABELS.get(r.family, r.family)] for r in inc.itertuples()],
        (14, 57, 69, 40), size=7.5)
    _texte(pdf, "Causes as entered in the MES, one per operation, not verified. Incident text is kept as in the source file.")
    return bytes(pdf.output())


def pdf_plm(ds: Dataset, res: R.Resultats) -> bytes:
    k = res.kpi
    pdf = _nouveau("PLM parts analysis", f"lead time Q3: {res.q3_delai:.2f} days")
    _kpis(pdf, [("Critical parts", f"{k['pieces_critiques']}", "criticality Critical", "#DC2626"),
                ("Long lead-time parts", f"{k['pieces_delai_eleve']}", f"lead time > Q3 ({res.q3_delai:.2f} d)", "#F97316"),
                ("Operations with a critical part", f"{k['operations_piece_critique']}", "at least one reference",
                 "#DC2626"),
                ("References not found", f"{k['references_inconnues']}", "missing from the PLM", GREY)])
    _texte(pdf, "Criticality and supply lead times, not stock-outs: a long lead time is not proof of late delivery.")
    _figure(pdf, fig_delais(res))
    p = res.pieces.sort_values(["critique", "delai_jours"], ascending=False)
    _titre(pdf, "Parts")
    _table(pdf, [["Reference", "Designation", "Criticality", "Lead time", "> Q3", "Supplier", "MES ops"]] + [
        [r.code, r.designation, r.criticality, r.lead_time, "yes" if r.delai_eleve else "", r.supplier, r.operations_mes]
        for r in p.itertuples()], (18, 42, 20, 22, 10, 50, 16), {2: R.COULEURS_CRITICITE}, size=7.5)
    return bytes(pdf.output())


def pdf_erp(ds: Dataset, res: R.Resultats, matricules: dict[int, str]) -> bytes:
    po, k = res.postes, res.kpi
    pdf = _nouveau("ERP resources analysis", "summaries by station and ERP employee IDs")
    _kpis(pdf, [("ERP resources", f"{k['ressources_erp']}", "distinct employee IDs", NAVY),
                ("Stations covered", f"{int((po['effectif'] > 0).sum())} / {len(po)}", "with ERP resources", BLUE),
                ("Average headcount", f"{po['effectif'].mean():.1f}", "declared, not present", BLUE),
                ("Unmatched stations", f"{k['postes_sans_erp']}", "missing from the ERP", GREY)])
    _texte(pdf, "No individual performance score: the ERP says neither who was present nor who carried out each "
                "operation.")
    _figure(pdf, fig_effectifs(res))
    niv = ds.erp["level"].value_counts()
    _texte(pdf, "Experience levels: " + ", ".join(f"{a} {b}" for a, b in niv.items()) + ".")
    _titre(pdf, "Stations")
    _table(pdf, [["Station", "Operation", "Headcount", "Levels", "Certifications", "Employee IDs"]] + [
        [r.poste, r.operation, r.effectif, r.niveaux, r.habilitations, matricules.get(r.poste, "")]
        for r in po.itertuples()], (14, 38, 16, 38, 34, 40), size=7)
    return bytes(pdf.output())


def pdf_finance(ds: Dataset, res: R.Resultats) -> bytes:
    f = res.fin
    pdf = _nouveau("Financial analysis", "PLM and ERP reference costs")
    _kpis(pdf, [("Total parts cost (FIN01)", eur(f["cout_total"]), f"{f['references_valorisees']} references",
                 "#16A34A"),
                ("Most expensive reference", f"{f['top_reference']} · {f['top_part_pct']:.1f}%",
                 f"{f['refs_80pct']} refs = over 80% of cost", "#DC2626"),
                ("Theoretical hourly cost (FIN04)", f"{f['fin04_moyen']:.2f} €/h",
                 f"station average, {f['fin04_min']:.0f} to {f['fin04_max']:.1f} €/h", BLUE),
                ("Invalid data", f"{f['donnees_invalides']}", "quantities or costs excluded", GREY)])
    _texte(pdf, "Reference costs computed from the PLM and the ERP: they are not financial losses caused by delays "
                "or errors.")
    _figure(pdf, fig_pareto(res))
    _titre(pdf, "Cost by reference (FIN02, FIN03)")
    v = res.fin_refs
    _table(pdf, [["Reference", "Designation", "Criticality", "Quantity", "Unit cost", "Total cost", "Share", "Cumul."]] + [
        [r.code, r.designation, r.criticality, int(r.qty_bom), eur(r.unit_cost_eur), eur(r.cout_reference),
         f"{r.part_pct:.2f}%", f"{r.part_cumulee_pct:.1f}%"] for r in v.itertuples()],
        (17, 38, 18, 15, 24, 28, 15, 15), {2: R.COULEURS_CRITICITE}, size=7.5)
    _figure(pdf, fig_fin04(res))
    _titre(pdf, "Hourly cost by station (FIN04, FIN05)")
    fp = res.fin_postes
    _table(pdf, [["Station", "Operation", "Resources", "Theoretical cost (€/h)", "Average cost (€/h)"]] + [
        [r.poste, r.operation, int(r.ressources_valides), f"{r.cout_horaire_theorique:.2f}", f"{r.cout_horaire_moyen:.2f}"]
        for r in fp.itertuples()], (14, 68, 20, 40, 38), size=7.5)
    _texte(pdf, "FIN04 assumes the station's resources work simultaneously: it is not an actual expense.")
    return bytes(pdf.output())


def resume_pdf(ds: Dataset, res: R.Resultats) -> bytes:
    ops, k = res.operations, res.kpi
    pdf = _nouveau("Anomaly summary - MSN-001",
                   f"PLM lead time Q3: {res.q3_delai:.2f} days · MES05 time tolerance: "
                   f"{R.TOLERANCE_HORAIRE_MIN:.0f} min")
    _kpis(pdf, [("Delayed operations", f"{int((ops['ecart_min'] > 0).sum())}", f"out of {len(ops)}", "#DC2626"),
                ("Cumulative overrun", f"{k['retard_cumule_min']:.1f} min", "", BLUE),
                ("High priorities", f"{k['priorites_elevees']}", "score ≥ 60", "#DC2626"),
                ("Provisional scores", f"{k['scores_provisoires']}", "insufficient data", GREY)])
    _titre(pdf, "Investigation priority scores")
    pr = R.resume_scores(res)
    _table(pdf, [["Priority", "Operations", "Average score"]] + [
        [r["priorite"], int(r["operations"]), f"{r['score_moyen']:.0f}/100" if r["operations"] else "-"]
        for _, r in pr.iterrows()], (40, 30, 30), {0: R.COULEURS_PRIORITE})
    pdf.ln(2)
    _top_ops(pdf, res, 15)
    prov = ops[ops["score_provisoire"]]
    if len(prov):
        _titre(pdf, "Provisional scores (missing data is not zero risk)")
        _table(pdf, [["Station", "Operation", "Score", "Limits", "Evidence"]] + [
            [r.poste, r.operation, r.score, r.limites_score, r.evidence] for r in prov.itertuples()],
            (14, 50, 12, 74, 20), size=8)
    _bloc_anomalies(pdf, ds, res)
    return bytes(pdf.output())
