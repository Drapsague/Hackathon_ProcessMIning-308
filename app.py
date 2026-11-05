"""Air+ | Industrial Performance Radar - application Streamlit (interface en anglais).

    streamlit run app.py
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import report as P
import rules as R
from assistant import DEMO_QUESTIONS, Assistant
from avion_3d import afficher_avion_3d
from etl import CAD_FILE, DATA_DIR, FAMILY_LABELS, load_all
from glossaire import GLOSSAIRE
from simulate import real_event_log, real_trace
from viewer3d import replay_html, viewer_html

ASSETS = Path(__file__).parent / "assets"
LOGO, ICON = ASSETS / "scout_logo.png", ASSETS / "scout_icon.png"

st.set_page_config(page_title="SCOUT | Air+ Industrial Performance Radar", layout="wide",
                   page_icon=str(ICON) if ICON.exists() else None,
                   initial_sidebar_state="collapsed")

NAVY, BLUE, GREY = "#1E3A8A", "#2563EB", "#94A3B8"
GRAV_AFFICHEES = R.GRAVITES
COULEURS_NIVEAU = {"Beginner": "#93C5FD", "Experienced": "#3B82F6", "Expert": "#1E3A8A"}

st.markdown("""
<style>
.stApp {background: #FFFFFF}
.block-container {padding: 3.2rem 2.2rem 3rem 2.2rem; max-width: 100%}
.hdr {border: 1px solid #E2E8F0; border-radius: 14px; padding: 14px 20px; display: flex; align-items: center;
      gap: 14px; margin-bottom: 6px; background: #FFFFFF}
.hdr .t {font-size: 1.55rem; font-weight: 700; color: #0F172A; line-height: 1.2}
.hdr .s {font-size: 0.9rem; color: #1E3A8A}
.hdr .logo {height: 76px; width: auto}
.hdr .sep {width: 1px; align-self: stretch; background: #E2E8F0; margin: 0 6px}
.hdr .k {font-size: 0.82rem; color: #475569; margin-top: 3px}
.hdr .k b {color: #0EA5E9}
.hdr .b {margin-left: auto; background: #EEF2FF; color: #1E3A8A; border-radius: 999px; padding: 4px 14px;
         font-weight: 600; font-size: 0.85rem}
.tile {border-radius: 14px; padding: 16px 18px; min-height: 112px; border: 1px solid; margin-bottom: 12px}
.tile .l {font-size: 0.9rem; color: #334155; font-weight: 600}
.tile .v {font-size: 2.1rem; font-weight: 800; color: #0F172A; line-height: 1.15}
.tile .s {font-size: 0.8rem; color: #475569}
.kpi {background: #FFFFFF; border-radius: 12px; padding: 14px 18px; min-height: 118px;
      margin-bottom: 12px}
.kpi-l {font-size: 0.86rem; color: #475569}
.kpi-v {font-size: 2rem; font-weight: 700; color: #0F172A; line-height: 1.25}
.kpi-s {font-size: 0.8rem; color: #64748B}
.note {border-left: 4px solid #F97316; background: #FFF7ED; padding: 10px 14px; border-radius: 6px;
       font-size: 0.9rem; margin: 8px 0 16px 0; color: #1F2937}
.leg {display: flex; align-items: center; gap: 10px; margin: 10px 0; font-size: 0.95rem; color: #1F2937}
.leg .sw {width: 14px; height: 14px; border-radius: 3px}
.leg .n {margin-left: auto; font-weight: 600}
div[data-testid="stTabs"] button p {font-size: 1rem; font-weight: 600}
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > .sec), .st-key-cadre {border-color: #E2E8F0}
div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px}
.sec {font-size: 1.08rem; font-weight: 700; color: #0F172A; margin: 0 0 2px 0}
.sec-s {font-size: 0.82rem; color: #64748B; margin-bottom: 8px}
div[data-testid="stDataFrame"] {border: 1px solid #E2E8F0; border-radius: 10px}
.st-key-chat_fab {position: fixed; right: 1.6rem; bottom: 1.4rem; z-index: 1000; width: auto !important}
.st-key-chat_fab button {width: 64px; height: 64px; border-radius: 50%; padding: 0; background: #1E3A8A;
    border: none; box-shadow: 0 8px 22px rgba(15,23,42,0.30)}
.st-key-chat_fab button {color: #FFFFFF}
.st-key-chat_fab button p, .st-key-chat_fab button p span {font-size: 2rem; line-height: 1}
.st-key-chat_fab button:hover {background: #1D4ED8}
.st-key-chat_fab button div[aria-hidden="true"] {display: none}
div[data-testid="stPopoverBody"] {width: min(500px, 92vw); max-height: 72vh; overflow-y: auto}
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Loading MES, PLM and ERP data…")
def load():
    ds = load_all(DATA_DIR)
    res = R.evaluer(ds)
    log, trace = real_event_log(ds), real_trace(ds)
    return ds, res, log, trace, Assistant(ds, log, trace, None, res)


ds, res, log, trace, assistant = load()
ops, kpi, fin = res.operations, res.kpi, res.fin
# matricules ERP déclarés sur chaque poste (les noms ne sont jamais chargés)
MATRICULES = ds.erp.groupby("poste")["operator_id"].apply(lambda s: ", ".join(sorted(s))).to_dict()


def num(x: float, nd: int = 1) -> str:
    return f"{x:,.{nd}f}"


def eur(x: float, nd: int = 0) -> str:
    return "€" + num(x, nd)


def tile(col, label: str, value: str, sub: str, fond: str, bord: str):
    col.markdown(f"<div class='tile' style='background:{fond};border-color:{bord}'>"
                 f"<div class='l'>{label}</div><div class='v'>{value}</div><div class='s'>{sub}</div></div>",
                 unsafe_allow_html=True)


# le jaune et le gris des palettes sont trop clairs pour un chiffre sur fond blanc
LISIBLE = {"#EAB308": "#CA8A04", GREY: "#64748B"}


def kpi_card(col, label: str, value: str, sub: str, accent: str = NAVY):
    c = LISIBLE.get(accent, accent)
    col.markdown(f"<div class='kpi' style='border:2px solid {c}'><div class='kpi-l'>{label}</div>"
                 f"<div class='kpi-v' style='color:{c}'>{value}</div><div class='kpi-s'>{sub}</div></div>",
                 unsafe_allow_html=True)


def cadre(titre: str, sous_titre: str = ""):
    """Section encadrée : titre, sous-titre, puis le contenu dans le with."""
    box = st.container(border=True)
    box.markdown(f"<div class='sec'>{titre}</div>" + (f"<div class='sec-s'>{sous_titre}</div>" if sous_titre else ""),
                 unsafe_allow_html=True)
    return box


PALETTES = {"Severity": R.COULEURS_GRAVITE, "Priority": R.COULEURS_PRIORITE, "Criticality": R.COULEURS_CRITICITE}


def couleur_cellule(v, palette: dict) -> str:
    c = palette.get(str(v))
    if not c:
        return ""
    ink = "#1F2937" if c in ("#EAB308", "#94A3B8") else "#FFFFFF"
    return f"background-color:{c};color:{ink};font-weight:600"


def tableau(df: pd.DataFrame, colores: list[str], fmt: dict | None = None, **kw):
    sty = df.style
    for col in colores:
        if col in df.columns:
            sty = sty.map(couleur_cellule, subset=[col], palette=PALETTES[col])
    if fmt:
        sty = sty.format(fmt, na_rep="-")
    return st.dataframe(sty, hide_index=True, **kw)


def barres(df: pd.DataFrame, x: str, y: str, couleurs: dict, ordre: list[str], titre_y: str, h: int = 240):
    base = alt.Chart(df).encode(
        x=alt.X(f"{x}:N", sort=ordre, title=None, axis=alt.Axis(labelAngle=0, labelFontSize=13)),
        y=alt.Y(f"{y}:Q", title=titre_y),
    )
    bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        color=alt.Color(f"{x}:N", scale=alt.Scale(domain=list(couleurs), range=list(couleurs.values())), legend=None),
        tooltip=[x, y])
    labels = base.mark_text(dy=-8, fontSize=14, fontWeight="bold").encode(text=f"{y}:Q")
    return (bars + labels).properties(height=h)


def ops_affichage(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Station": df["poste"], "Operation": df["operation"], "Date": df["start"].dt.strftime("%d/%m"),
        "Planned (min)": df["planned_min"], "Actual (min)": df["real_min"], "Gap (min)": df["ecart_min"],
        "Delay (%)": df["retard_pct"], "Severity": df["gravite"], "Declared incident": df["alea"],
        "Critical parts": df["pieces_critiques"].map(", ".join),
        "Long lead time": df["pieces_delai_eleve"].map(", ".join),
        "Score": df["score"], "Priority": df["priorite"],
        "Provisional": df["score_provisoire"].map({True: "yes", False: ""}), "Evidence": df["evidence"],
        "Station employee IDs": df["poste"].map(MATRICULES).fillna(""),
    })


FMT_OPS = {"Planned (min)": "{:.0f}", "Actual (min)": "{:.2f}", "Gap (min)": "{:+.2f}", "Delay (%)": "{:.1f}"}
INCIDENT = ["All", "With incident", "Without incident"]


# Les PDF sont calculés une fois puis gardés en cache (matplotlib est lent à chaque rerun)
@st.cache_data(show_spinner="Preparing PDF…")
def pdf_page(page: str, filtre: tuple = ()) -> bytes:
    if page == "vue":
        return P.pdf_vue_globale(ds, res)
    if page == "resume":
        return P.resume_pdf(ds, res)
    if page == "plm":
        return P.pdf_plm(ds, res)
    if page == "erp":
        return P.pdf_erp(ds, res, MATRICULES)
    if page == "fin":
        return P.pdf_finance(ds, res)
    postes, dates, grav, inc = filtre
    v = filtrer(ops, list(postes), list(dates), list(grav), inc)
    txt = ", ".join(t for t in (
        f"stations {', '.join(map(str, postes))}" if postes else "", f"severity {', '.join(grav)}" if grav else "",
        inc.lower() if inc != "All" else "") if t)
    return P.pdf_production(ds, res, v, txt)


def bouton_pdf(col, page: str, nom: str, filtre: tuple = ()):
    col.download_button("Export page (PDF)", pdf_page(page, filtre), f"airplus_{nom}.pdf", "application/pdf",
                        use_container_width=True, key=f"pdf_{page}")


def filtrer(df, postes, dates, grav, inc):
    v = df
    if postes:
        v = v[v["poste"].isin(postes)]
    if dates:
        v = v[v["start"].dt.date.isin(dates)]
    if grav:
        v = v[v["gravite"].isin(grav)]
    if inc != "All":
        v = v[v["incident"] == (inc == "With incident")]
    return v


def resume_anomalies():
    pr = R.resume_scores(res)
    c = st.columns(3)
    for col, (_, r) in zip(c, pr.iterrows()):
        sub = f"average score {num(r['score_moyen'], 0)}/100" if r["operations"] else "no operation"
        kpi_card(col, f"{r['priorite']} priority", f"{int(r['operations'])}", sub, R.COULEURS_PRIORITE[r["priorite"]])
    prov = ops[ops["score_provisoire"]]
    if len(prov):
        st.markdown("**Provisional scores**: insufficient data, missing data is not zero risk.")
        st.dataframe(prov[["poste", "operation", "score", "limites_score", "evidence"]].rename(columns={
            "poste": "Station", "operation": "Operation", "score": "Score", "limites_score": "Limits",
            "evidence": "Evidence"}), hide_index=True)
    st.markdown("**Investigation signals** (cross-checks, never causes)")
    st.dataframe(pd.DataFrame([
        ("CROSS01", "Delay + critical part", int(ops["CROSS01"].sum())),
        ("CROSS02", f"Delay + part with lead time > Q3 ({num(res.q3_delai, 2)} d)", int(ops["CROSS02"].sum())),
        ("CROSS03", "Delay + declared incident", int(ops["CROSS03"].sum())),
        ("CROSS04", "Delay + ERP resources on the station", int(ops["CROSS04"].sum())),
    ], columns=["Rule", "Signal", "Operations"]), hide_index=True)
    st.markdown("**Anomalies detected by the rules**")
    if res.anomalies:
        st.dataframe(pd.DataFrame([vars(a) for a in res.anomalies]).rename(columns={
            "regle": "Rule", "objet": "Object", "detail": "Detail", "source": "Evidence"}), hide_index=True)
    else:
        st.write("None.")
    controles = [f for f in ds.findings if f.severity in ("haute", "moyenne")]
    st.markdown(f"**Cross-source consistency checks** ({len(controles)})")
    for f in controles:
        st.markdown(f"- **{f.id} · {f.title}** - {f.detail}")
    d1, d2 = st.columns(2)
    d1.download_button("Save summary as PDF", pdf_page("resume"), "airplus_anomaly_summary.pdf",
                       "application/pdf", type="primary", use_container_width=True)
    d2.download_button("Score details (CSV)", ops_affichage(ops).to_csv(index=False).encode("utf-8-sig"),
                       "airplus_scores.csv", "text/csv", use_container_width=True)


_dialog = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)
resume_dialog = _dialog("Anomaly summary", width="large")(resume_anomalies) if _dialog else None

logo = (f"<img class='logo' src='data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}' "
        "alt='SCOUT Air+'><div class='sep'></div>") if LOGO.exists() else ""
st.markdown(f"<div class='hdr'>{logo}<div><div class='t'>Air+ | Industrial Performance Radar</div>"
            "<div class='s'>Business rules v1.2 applied to the actual MES, PLM and ERP extracts</div>"
            "<div class='k'>SCOUT: <b>S</b>ignal · <b>C</b>ost · <b>O</b>verrun · <b>U</b>rgency · <b>T</b>ime, "
            "spotting the delay before it happens</div></div><div class='b'>Prototype</div></div>",
            unsafe_allow_html=True)

t_vue, t_prod, t_plm, t_erp, t_fin, t_sim, t_glo = st.tabs(
    ["Overview", "Production", "PLM parts", "ERP teams", "Financial analysis", "Simulation", "Glossary"])


def entete(col_titre, titre: str):
    col_titre.markdown(f"#### {titre}")


with t_vue:
    a, b, c = st.columns([2.6, 1.2, 1.2])
    entete(a, "Overview - Key indicators")
    if b.button("Anomaly summary", type="primary", use_container_width=True):
        if resume_dialog:
            resume_dialog()
        else:
            st.session_state["resume"] = not st.session_state.get("resume", False)
    bouton_pdf(c, "vue", "overview")
    if not resume_dialog and st.session_state.get("resume"):
        with st.container(border=True):
            resume_anomalies()

    analysables = int(ops["retard_pct"].notna().sum())
    n_retard = int((ops["ecart_min"] > 0).sum())
    prov_eleve = int((ops["score_provisoire"] & (ops["score"] >= 60)).sum())
    t = st.columns(4)
    tile(t[0], "Delayed operations", f"{n_retard}",
         f"out of {analysables} analysable ({num(kpi['taux_retard'], 0)}%)", "#FEF2F2", "#FECACA")
    tile(t[1], "Cumulative overrun", f"{num(kpi['retard_cumule_min'], 1)} min",
         f"i.e. {num(kpi['retard_cumule_min'] / 60, 1)} h · Σ max(0, actual - planned)", "#EFF6FF", "#BFDBFE")
    tile(t[2], "High-priority operations", f"{kpi['priorites_elevees']}",
         f"P04 score ≥ 60 · {prov_eleve} provisional score(s)", "#F5F3FF", "#DDD6FE")
    tile(t[3], "Total cost of required parts (FIN01)", eur(kpi["cout_total_pieces"]),
         "Σ PLM quantity × purchase cost", "#F0FDF4", "#BBF7D0")

    g1, g2 = st.columns(2)
    with g1, cadre("Duration overrun by station (MES)", "5 stations with the largest Σ max(0, actual - planned)"):
        top5 = R.depassement_par_poste(res).head(5).copy()
        top5["libelle"] = "Station " + top5["poste"].astype(str)
        top5["txt"] = top5["dep"].map(lambda x: num(x, 2) + " min")
        base = alt.Chart(top5).encode(
            y=alt.Y("libelle:N", sort=top5["libelle"].tolist(), title=None, axis=alt.Axis(labelFontSize=13)),
            x=alt.X("dep:Q", title="Overrun (minutes)", scale=alt.Scale(domain=[0, top5["dep"].max() * 1.2])))
        st.altair_chart((base.mark_bar(color=BLUE, cornerRadiusEnd=4, height=26).encode(
            tooltip=[alt.Tooltip("poste", title="station"), alt.Tooltip("operation", title="operation"),
                     alt.Tooltip("dep", format=".2f", title="overrun (min)")])
            + base.mark_text(align="left", dx=6, fontSize=13, fontWeight="bold").encode(text="txt:N"))
            .properties(height=260), use_container_width=True)
        st.caption(" · ".join(f"{r.poste} {r.operation}" for r in top5.itertuples()))
    with g2, cadre("Operations by priority level (P04)", "Low 0-29 · medium 30-59 · high 60-100"):
        counts = ops["priorite"].value_counts().reindex(R.PRIORITES, fill_value=0)
        prov = ops[ops["score_provisoire"]]["priorite"].value_counts()
        dn = pd.DataFrame({"priorite": R.PRIORITES, "n": [int(counts[k]) for k in R.PRIORITES]})
        x1, x2 = st.columns([1.05, 1])
        arc = alt.Chart(dn[dn["n"] > 0]).mark_arc(innerRadius=72, outerRadius=118, stroke="#FFFFFF", strokeWidth=2).encode(
            theta=alt.Theta("n:Q", stack=True),
            color=alt.Color("priorite:N", scale=alt.Scale(domain=R.PRIORITES,
                                                         range=[R.COULEURS_PRIORITE[k] for k in R.PRIORITES]), legend=None),
            order=alt.Order("priorite:N", sort="descending"),
            tooltip=[alt.Tooltip("priorite", title="priority"), alt.Tooltip("n", title="operations")])
        centre = alt.Chart(pd.DataFrame({"t": [str(len(ops))]})).mark_text(fontSize=30, fontWeight="bold", dy=-8).encode(text="t:N")
        centre2 = alt.Chart(pd.DataFrame({"t": ["operations"]})).mark_text(fontSize=13, dy=18, color="#475569").encode(text="t:N")
        x1.altair_chart((arc + centre + centre2).properties(height=260, width=260))
        bornes = {"High": "≥ 60", "Medium": "30 - 59", "Low": "0 - 29"}
        leg = ""
        for k in reversed(R.PRIORITES):
            p_txt = f"<br><span style='font-size:0.8rem;color:#64748B'>incl. {prov[k]} provisional score</span>" if prov.get(k) else ""
            leg += (f"<div class='leg'><span class='sw' style='background:{R.COULEURS_PRIORITE[k]}'></span>"
                    f"<span>{k} priority ({bornes[k]}){p_txt}</span><span class='n'>{counts[k]} "
                    f"({num(counts[k] / len(ops) * 100, 1)}%)</span></div>")
        x2.markdown(f"<div style='margin-top:40px'>{leg}</div>", unsafe_allow_html=True)
        st.caption(f"{kpi['scores_provisoires']} provisional score: insufficient data for a full score, counted in its "
                   "priority but flagged separately.")

with t_prod:
    a, c = st.columns([3.8, 1.2])
    entete(a, "MES production analysis - where the delays are")
    row1, row2 = st.columns(4), st.columns(4)
    kpi_card(row1[0], "Number of operations", f"{kpi['operations']}", "valid MES rows, no duplicates")
    kpi_card(row1[1], "Delay rate", f"{num(kpi['taux_retard'], 0)}%",
             f"{int((ops['ecart_min'] > 0).sum())} out of {int(ops['retard_pct'].notna().sum())} analysable",
             R.COULEURS_GRAVITE["critical"])
    kpi_card(row1[2], "Major delays", f"{kpi['retards_importants']}", "delay ≥ 20% and < 50%",
             R.COULEURS_GRAVITE["major"])
    kpi_card(row1[3], "Critical delays", f"{kpi['retards_critiques']}", "delay ≥ 50%", R.COULEURS_GRAVITE["critical"])
    h, mn = divmod(round(kpi["retard_cumule_min"]), 60)
    kpi_card(row2[0], "Cumulative delay", f"{num(kpi['retard_cumule_min'], 1)} min", f"{h} h {mn:02d} of overruns",
             R.COULEURS_GRAVITE["major"])
    kpi_card(row2[1], "Declared incidents", f"{kpi['incidents']}", "operations with a recorded incident", "#EAB308")
    kpi_card(row2[2], "High priorities", f"{kpi['priorites_elevees']}", "investigation score ≥ 60",
             R.COULEURS_PRIORITE["High"])
    kpi_card(row2[3], "Provisional scores", f"{kpi['scores_provisoires']}", "insufficient data", GREY)

    g1, g2 = st.columns([1, 2])
    with g1, cadre("Delays by severity", "minor < 20% · major 20-50% · critical ≥ 50%"):
        gv = ops["gravite"].value_counts().reindex(GRAV_AFFICHEES, fill_value=0).rename_axis("severity").reset_index(name="operations")
        st.altair_chart(barres(gv, "severity", "operations", {k: R.COULEURS_GRAVITE[k] for k in GRAV_AFFICHEES},
                               GRAV_AFFICHEES, "operations", 290), use_container_width=True)
    with g2, cadre("Operations to investigate first", "P04 score = delay (0, 10, 20 or 40) + incident (25) + critical "
                   "part (20) + lead time > Q3 (15), capped at 100 · it says where to look, not why"):
        top = ops.sort_values(["score", "retard_pct"], ascending=False).head(10)
        tableau(ops_affichage(top)[["Station", "Operation", "Delay (%)", "Severity", "Critical parts", "Long lead time",
                                    "Score", "Priority", "Provisional"]], ["Severity", "Priority"], FMT_OPS, height=300)

    with cadre("Average delay by station", "Filter to narrow the scope; filters also apply to the tables and the PDF "
               "export"):
        f1, f2, f3, f4 = st.columns([2, 1.2, 1.5, 1.2])
        postes = f1.multiselect("Stations", ops["poste"].tolist(), placeholder="All stations")
        dates = sorted(ops["start"].dt.date.unique())
        dsel = f2.multiselect("Dates", dates, format_func=lambda d: d.strftime("%d/%m/%Y"), placeholder="All")
        gsel = f3.multiselect("Severity", GRAV_AFFICHEES, placeholder="All")
        isel = f4.selectbox("Incident", INCIDENT)
        v = filtrer(ops, postes, dsel, gsel, isel)
        if len(v):
            pm = v.groupby(["poste", "operation"], as_index=False).agg(retard=("retard_pct", "mean"))
            pm["severity"] = pm["retard"].map(R.gravite)
            bars = alt.Chart(pm).mark_bar().encode(
                x=alt.X("poste:O", title="Station (routing order)", axis=alt.Axis(labelAngle=0)),
                y=alt.Y("retard:Q", title="delay (%)"),
                color=alt.Color("severity:N", scale=alt.Scale(domain=GRAV_AFFICHEES,
                                                              range=[R.COULEURS_GRAVITE[g] for g in GRAV_AFFICHEES]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=[alt.Tooltip("poste", title="station"), alt.Tooltip("operation", title="operation"),
                         alt.Tooltip("retard", format=".1f", title="delay (%)"), "severity"])
            seuils = alt.Chart(pd.DataFrame({"y": [20, 50]})).mark_rule(strokeDash=[6, 4], color=GREY).encode(y="y:Q")
            st.altair_chart((bars + seuils).properties(height=280), use_container_width=True)
            st.caption("Dashed lines: 20% (major) and 50% (critical) thresholds.")
        else:
            st.info("No operation matches the filters.")
    bouton_pdf(c, "prod", "production", (tuple(postes), tuple(dsel), tuple(gsel), isel))

    with cadre(f"All operations ({len(v)})", "Employee IDs: resources declared in the ERP for the station. The MES "
               "does not say which one carried out the operation."):
        tableau(ops_affichage(v)[["Station", "Operation", "Date", "Planned (min)", "Actual (min)", "Gap (min)",
                                  "Delay (%)", "Severity", "Priority", "Evidence", "Station employee IDs"]],
                ["Severity", "Priority"], FMT_OPS, height=380)

    with cadre("Declared industrial incidents", "Causes as entered in the MES: one per operation, not verified, text "
               "kept as in the source file"):
        inc = v[v["incident"]].merge(ds.mes[["poste", "family"]], on="poste")
        st.dataframe(pd.DataFrame({
            "Station": inc["poste"], "Operation": inc["operation"], "Declared incident": inc["alea"],
            "Declared potential cause": inc["cause"], "Family": inc["family"].map(FAMILY_LABELS),
            "Evidence": inc["evidence"],
        }), hide_index=True, height=300)
        mes05 = [x for x in res.anomalies if x.regle == "MES05"]
        if mes05:
            st.markdown(f"<div class='note'><b>Time consistency (MES05)</b>: {len(mes05)} operations where end time - "
                        f"start time does not match the declared actual time (stations "
                        f"{', '.join(x.objet.split()[-1] for x in mes05)}). The MES is not corrected.</div>",
                        unsafe_allow_html=True)

with t_plm:
    a, c = st.columns([3.8, 1.2])
    entete(a, "PLM parts analysis")
    bouton_pdf(c, "plm", "plm_parts")
    k4 = st.columns(4)
    kpi_card(k4[0], "Critical parts", f"{kpi['pieces_critiques']}", "references with \"Critical\" criticality",
             R.COULEURS_CRITICITE["Critical"])
    kpi_card(k4[1], "Long lead-time parts", f"{kpi['pieces_delai_eleve']}", f"lead time > Q3 = {num(res.q3_delai, 2)} days",
             R.COULEURS_CRITICITE["High"])
    kpi_card(k4[2], "Operations with a critical part", f"{kpi['operations_piece_critique']}",
             "MES operations with at least one critical reference", R.COULEURS_CRITICITE["Critical"])
    kpi_card(k4[3], "References not found", f"{kpi['references_inconnues']}", "MES references missing from the PLM", GREY)
    st.markdown("<div class='note'>This page is about criticality and supply lead times, not stock-outs: a long lead "
                "time is not proof of late delivery, and the data does not allow a stock-out rate to be "
                "computed.</div>", unsafe_allow_html=True)

    p = res.pieces.copy()
    with cadre("Supply lead time by reference", f"Red dashed line: Q3 = {num(res.q3_delai, 2)} days (upper bound of "
               "the ranges). Above it, the lead time is \"long\" (PLM02)"):
        ordre = p.sort_values("delai_jours", ascending=False)["code"].tolist()
        bars = alt.Chart(p).mark_bar().encode(
            x=alt.X("code:N", sort=ordre, title=None, axis=alt.Axis(labelAngle=-90)),
            y=alt.Y("delai_jours:Q", title="lead time used (days)"),
            color=alt.Color("criticality:N", title="Criticality", scale=alt.Scale(
                domain=list(R.COULEURS_CRITICITE), range=list(R.COULEURS_CRITICITE.values())),
                legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("code", title="reference"), alt.Tooltip("designation", title="designation"),
                     alt.Tooltip("criticality", title="criticality"), alt.Tooltip("lead_time", title="lead time"),
                     alt.Tooltip("supplier", title="supplier"), alt.Tooltip("operations_mes", title="MES operations")])
        q3 = alt.Chart(pd.DataFrame({"y": [res.q3_delai]})).mark_rule(strokeDash=[6, 4], color="#DC2626").encode(y="y:Q")
        st.altair_chart((bars + q3).properties(height=300), use_container_width=True)

    with cadre("Parts master"):
        f1, f2 = st.columns([2, 1])
        crit = f1.multiselect("Criticality", list(R.COULEURS_CRITICITE), placeholder="All")
        seul = f2.toggle("Only lead times > Q3")
        if crit:
            p = p[p["criticality"].isin(crit)]
        if seul:
            p = p[p["delai_eleve"]]
        tableau(pd.DataFrame({
            "Reference": p["code"], "Designation": p["designation"], "Criticality": p["criticality"],
            "Supply lead time": p["lead_time"], "Lead time > Q3": p["delai_eleve"].map({True: "yes", False: ""}),
            "Supplier": p["supplier"], "Related MES operations": p["operations_mes"], "Evidence": p["evidence"],
        }), ["Criticality"], height=360)

    with cadre("A part in the 3D model", "Quantities according to the manual, the PLM, the MES and the CAD model"):
        if st.toggle("Show the model", key="piece3d"):
            codes = res.pieces["code"].tolist()
            code = st.selectbox("Reference", codes, index=codes.index("D144"),
                                format_func=lambda x: f"{x} · {ds.plm.set_index('code').loc[x, 'designation']}")
            l, r = st.columns([1, 1.4])
            bom = ds.bom.set_index("code").loc[code]
            l.dataframe(pd.DataFrame({"Source": ["Manual (p. 43)", "PLM", "MES (MSN-001)", "3D model"],
                                      "Quantity": [bom["notice"], bom["plm"], bom["mes"], bom["cad"]]}), hide_index=True)
            for f in ds.findings:
                if code in f.detail:
                    l.warning(f"**{f.id} · {f.title}** - {f.detail}")
            glb = DATA_DIR / CAD_FILE
            with r:
                if glb.exists():
                    components.html(viewer_html(glb, [code]), height=440)
                else:
                    st.info("Model missing: run `python tools/step_to_glb.py` (see README).")

with t_erp:
    a, c = st.columns([3.8, 1.2])
    entete(a, "ERP resources analysis")
    bouton_pdf(c, "erp", "erp_teams")
    po = res.postes
    k4 = st.columns(4)
    kpi_card(k4[0], "ERP resources", f"{kpi['ressources_erp']}", "distinct employee IDs")
    kpi_card(k4[1], "Stations covered", f"{int((po['effectif'] > 0).sum())} / {len(po)}", "MES stations with ERP resources")
    kpi_card(k4[2], "Average headcount per station", num(po["effectif"].mean(), 1), "declared, not present")
    kpi_card(k4[3], "Unmatched stations", f"{kpi['postes_sans_erp']}", "MES stations missing from the ERP", GREY)
    st.markdown("<div class='note'>No individual performance score: the ERP says neither who was present nor who "
                "carried out each operation. Headcounts are declared, not measured.</div>", unsafe_allow_html=True)
    g1, g2 = st.columns([2, 1])
    with g1, cadre("ERP headcount by station"):
        st.altair_chart(alt.Chart(po).mark_bar(color=BLUE).encode(
            x=alt.X("poste:O", title="Station", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("effectif:Q", title="employee IDs", axis=alt.Axis(tickMinStep=1)),
            tooltip=[alt.Tooltip("poste", title="station"), alt.Tooltip("operation", title="operation"),
                     alt.Tooltip("effectif", title="headcount"), alt.Tooltip("niveaux", title="levels")])
            .properties(height=260), use_container_width=True)
    with g2, cadre("Experience levels"):
        nv = ds.erp["level"].value_counts().reindex(list(COULEURS_NIVEAU), fill_value=0).rename_axis("level").reset_index(name="resources")
        st.altair_chart(barres(nv, "level", "resources", COULEURS_NIVEAU, list(COULEURS_NIVEAU), "resources", 260),
                        use_container_width=True)
    with cadre("Stations and employee IDs", "Click a row for the station details"):
        vue = pd.DataFrame({"Station": po["poste"], "Operation": po["operation"], "Headcount": po["effectif"],
                            "Experience levels": po["niveaux"], "Employee IDs": po["poste"].map(MATRICULES).fillna("")})
        try:
            ev = st.dataframe(vue, hide_index=True, height=300, on_select="rerun", selection_mode="single-row",
                              key="erp_sel")
            rows = ev.selection.rows
        except TypeError:  # Streamlit trop ancien pour la sélection de ligne
            st.dataframe(vue, hide_index=True, height=300)
            rows = []
        choix = int(po.iloc[rows[0]]["poste"]) if rows else st.selectbox(
            "Or pick a station", po["poste"].tolist(), format_func=lambda x: f"Station {x}")
        d = po.set_index("poste").loc[choix]
        with st.container(border=True):
            st.markdown(f"**Station {choix} · {d['operation']}** - {d['effectif']} declared resources: "
                        f"{MATRICULES.get(choix, '-')}")
            x1, x2 = st.columns(2)
            x1.markdown(f"**Qualifications**: {d['qualifications']}")
            x1.markdown(f"**Experience levels**: {d['niveaux']}")
            x2.markdown(f"**Skills**: {d['competences']}")
            x2.markdown(f"**Certifications**: {d['habilitations']}")
            st.caption(f"Evidence {d['evidence']} · qualifications, skills and certifications as written in the ERP")

with t_fin:
    a, c = st.columns([3.8, 1.2])
    entete(a, "Financial analysis - reference costs")
    bouton_pdf(c, "fin", "financial_analysis")
    t = st.columns(4)
    tile(t[0], "Total parts cost (FIN01)", eur(fin["cout_total"]),
         f"{fin['references_valorisees']} references · Σ quantity × cost", "#F0FDF4", "#BBF7D0")
    tile(t[1], "Most expensive reference (FIN03)", f"{fin['top_reference']} · {num(fin['top_part_pct'], 1)}%",
         f"{fin['refs_80pct']} references are enough to exceed 80% of the cost", "#FEF2F2", "#FECACA")
    tile(t[2], "Average theoretical hourly cost (FIN04)", f"{num(fin['fin04_moyen'], 2)} €/h",
         f"per station, from {num(fin['fin04_min'], 0)} to {num(fin['fin04_max'], 1)} €/h", "#EFF6FF", "#BFDBFE")
    tile(t[3], "Invalid data", f"{fin['donnees_invalides']}",
         "missing, non-numeric or negative quantities or costs", "#F8FAFC", "#E2E8F0")
    st.markdown("<div class='note'>Reference costs computed from the PLM and the ERP. They are not financial losses "
                "caused by delays or errors. FIN04 assumes the station's resources work simultaneously: it is not an "
                "actual expense.</div>", unsafe_allow_html=True)

    v = res.fin_refs.copy()
    with cadre("Cost by reference (FIN02) and cumulative budget share (FIN03)",
               f"{fin['top_reference']} and the next reference account for {num(fin['top2_part_pct'], 1)}% of the "
               "total cost · dashed line: 80% of the budget"):
        toutes = st.toggle("Show all 40 references in the chart", value=False)
        vg = v if toutes else v.head(15)
        ordre = vg["code"].tolist()
        base = alt.Chart(vg).encode(x=alt.X("code:N", sort=ordre, title=None, axis=alt.Axis(labelAngle=-45)))
        bars = base.mark_bar().encode(
            y=alt.Y("cout_reference:Q", title="cost (€)", axis=alt.Axis(format="~s")),
            color=alt.Color("criticality:N", title="Criticality", scale=alt.Scale(
                domain=list(R.COULEURS_CRITICITE), range=list(R.COULEURS_CRITICITE.values())),
                legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("code", title="reference"), alt.Tooltip("designation", title="designation"),
                     alt.Tooltip("cout_reference", format=",.0f", title="cost (€)"),
                     alt.Tooltip("part_pct", format=".2f", title="share (%)")])
        ligne = base.mark_line(color=NAVY, point=True).encode(
            y=alt.Y("part_cumulee_pct:Q", title="cumulative share (%)", scale=alt.Scale(domain=[0, 100])),
            tooltip=[alt.Tooltip("part_cumulee_pct", format=".1f", title="cumulative (%)")])
        seuil = alt.Chart(pd.DataFrame({"y": [80]})).mark_rule(strokeDash=[6, 4], color=GREY).encode(y="y:Q")
        st.altair_chart(alt.layer(bars, alt.layer(ligne, seuil)).resolve_scale(y="independent").properties(height=320),
                        use_container_width=True)
        tableau(pd.DataFrame({
            "Reference": v["code"], "Designation": v["designation"], "Criticality": v["criticality"],
            "Quantity": v["qty_bom"], "Unit cost (€)": v["unit_cost_eur"], "Total cost (€)": v["cout_reference"],
            "Share (%)": v["part_pct"], "Cumulative share (%)": v["part_cumulee_pct"], "Evidence": v["evidence"],
        }), ["Criticality"], {"Unit cost (€)": "{:,.0f}", "Total cost (€)": "{:,.0f}", "Share (%)": "{:.2f}",
                              "Cumulative share (%)": "{:.1f}"}, height=330)

    fp = res.fin_postes
    with cadre("Theoretical hourly cost by station (FIN04) and average resource cost (FIN05)",
               f"Dashed line: station average, {num(fin['fin04_moyen'], 2)} €/h · average cost of a resource "
               f"{num(fin['fin05_moyen'], 2)} €/h"):
        moy = alt.Chart(pd.DataFrame({"y": [fin["fin04_moyen"]]})).mark_rule(strokeDash=[6, 4], color=GREY).encode(y="y:Q")
        st.altair_chart((alt.Chart(fp).mark_bar(color=BLUE).encode(
            x=alt.X("poste:O", title="Station", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("cout_horaire_theorique:Q", title="€ / hour"),
            tooltip=[alt.Tooltip("poste", title="station"), alt.Tooltip("operation", title="operation"),
                     alt.Tooltip("cout_horaire_theorique", format=".2f", title="FIN04 (€/h)"),
                     alt.Tooltip("cout_horaire_moyen", format=".2f", title="FIN05 (€/h)"),
                     alt.Tooltip("ressources_valides", title="resources")])
            + moy).properties(height=260), use_container_width=True)
        st.dataframe(pd.DataFrame({
            "Station": fp["poste"], "Operation": fp["operation"], "Resources": fp["ressources_valides"],
            "Theoretical hourly cost (€/h)": fp["cout_horaire_theorique"].round(2),
            "Average hourly cost (€/h)": fp["cout_horaire_moyen"].round(2), "Evidence": fp["evidence"],
        }), hide_index=True, height=300)

with t_sim:
    entete(st, "Simulation - the aircraft in 3D")
    with cadre("3D assembly workshop", "Rotate, zoom, click a part to pull it out; Production and Criticality modes "
               "linked to the MES and the PLM through the reference"):
        # 9 Mo de maillages : chargé à la demande pour ne pas ralentir les autres onglets
        if st.toggle("Load the 3D workshop", key="atelier3d"):
            afficher_avion_3d(hauteur=850)
            st.caption("Geometry from the supplied STEP file (99 shapes). Extraction and exploded view are presentation "
                       "animations, not a physical simulation. An action elsewhere on the page may reset the view.")
    with cadre("Replay the production line", "MSN-001, operations in MES order and with their actual durations"):
        if st.toggle("Start the line simulation", key="replay"):
            glb = DATA_DIR / CAD_FILE
            if glb.exists():
                refs_all = ds.mes.set_index("poste")["refs"]
                seq = [dict(poste=int(x.poste), name=x.operation, date=x.start.strftime("%d/%m %H:%M"),
                            planned=float(x.planned_min), real=float(x.real_min), pct=float(x.retard_pct),
                            grav=x.gravite, color=R.COULEURS_GRAVITE[x.gravite], alea=x.alea,
                            refs=list(x.refs_distinctes), refs_all=list(refs_all[x.poste]))
                       for x in ops.sort_values("start").itertuples()]
                components.html(replay_html(glb, seq, dict(zip(ds.plm["code"], ds.plm["criticality"]))), height=600)
                st.caption("Nights excluded. Parts of the current operation are coloured, parts already fitted turn "
                           "green. Fasteners are not modelled; symmetric instances go to the right side.")
            else:
                st.info("Model missing: run `python tools/step_to_glb.py` (see README).")

with t_glo:
    entete(st, "Glossary")
    g1, g2 = st.columns([2, 1])
    cherche = g1.text_input("Search a term", placeholder="e.g. Q3, score, FIN04, employee ID…")
    cats = list(dict.fromkeys(c for c, _, _ in GLOSSAIRE))
    csel = g2.multiselect("Topic", cats, placeholder="All topics")
    n = 0
    for cat in cats:
        if csel and cat not in csel:
            continue
        lignes = [(t, d) for c, t, d in GLOSSAIRE if c == cat and (not cherche or cherche.lower() in (t + " " + d).lower())]
        if not lignes:
            continue
        n += len(lignes)
        with cadre(cat):
            for terme, definition in lignes:
                st.markdown(f"**{terme}** - {definition}")
    if not n:
        st.info("No term matches the search.")


def message(a):
    with st.chat_message("user"):
        st.write(a.question)
    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        if a.refused:
            st.error(a.text)
        else:
            for s in a.sentences:
                if s.ok and s.interpretation:
                    st.markdown(f"*{s.text.strip()}*")
                elif s.ok:
                    body = re.sub(r"\s*\[[^\]]+\]", "", s.text).strip()
                    st.markdown(f"{body} " + " ".join(f"`{i}`" for i in s.ids))
        with st.expander(f"Cited evidence ({len(a.evidence)})"):
            for e in a.evidence.values():
                st.markdown(f"`{e.id}` · {e.source}  \n{e.text}")
        with st.expander("Pipeline and checks"):
            st.table(pd.DataFrame(a.steps, columns=["agent", "action"]))
            if a.sentences:
                st.dataframe(pd.DataFrame([{"sentence": s.text[:120], "citations": ", ".join(s.ids),
                                            "validated": s.ok, "hypothesis": s.interpretation,
                                            "issues": "; ".join(s.issues)} for s in a.sentences]),
                             hide_index=True)


def chat_panel():
    st.markdown("#### AI assistant")
    st.caption(f"Every sentence cites its source; hypotheses in italics · writer: {assistant.writer.mode}")
    if "history" not in st.session_state:
        st.session_state.history = []
    with st.expander("Example questions", expanded=not st.session_state.get("history")):
        cols = st.columns(2)
        for i, q in enumerate(DEMO_QUESTIONS):
            if cols[i % 2].button(q, key=f"demo{i}", use_container_width=True):
                st.session_state.pending = q
    # la conversation se lit de haut en bas, la saisie reste en dessous : on réserve la place des messages
    # avant le formulaire, puis on les remplit une fois la nouvelle question traitée
    fil = st.container()
    with st.form("question", clear_on_submit=True):
        q1, q2 = st.columns([3.6, 1.4])
        saisie = q1.text_input("Question", placeholder="Ask a question about Air+ production",
                               label_visibility="collapsed")
        envoyer = q2.form_submit_button("Send", use_container_width=True)
    q = saisie if envoyer and saisie.strip() else st.session_state.pop("pending", None)
    if q:
        with st.spinner("Analysing…"):
            st.session_state.history.append(assistant.ask(q))
    with fil:
        for a in st.session_state.history[-6:]:
            message(a)
    if st.session_state.history:
        # fait défiler la fenêtre jusqu'à la dernière réponse
        components.html(f"""<script>
        const b = window.parent.document.querySelector('[data-testid="stPopoverBody"]');
        if (b) setTimeout(() => b.scrollTo({{top: b.scrollHeight, behavior: "smooth"}}), 150);
        // {len(st.session_state.history)}
        </script>""", height=0)
    with st.expander("AI rules applied (specification, IA01 to IA08)"):
        st.markdown(
            "- **IA01 No invention / IA05 Insufficient data**: the checker removes any figure without evidence; with "
            "no relevant fact, the assistant refuses.\n"
            "- **IA02 Traceability**: every sentence cites an MES row, a PLM reference, a manual page or a finding.\n"
            "- **IA03 Facts / hypotheses**: hypotheses start with \"Hypothesis:\", contain no figures and are shown "
            "in italics.\n"
            "- **IA04 No individual attribution**: the assistant gives the station's ERP employee IDs, never a "
            "judgement on a person; the MES does not say who carried out the operation.\n"
            "- **IA06 Verifiable recommendations**: every proposed action cites the data behind it.\n"
            "- **IA07 Confidentiality**: ERP first names, last names and comments are never loaded; the LLM receives "
            "employee IDs and per-station aggregates.\n"
            "- **IA08 Deterministic calculations**: Python computes (rules.py), the AI only writes. For a question with "
            "no planned calculation, the LLM writes a pandas expression that Python runs in a locked sandbox; the "
            "expression and its result are shown in the evidence.")


with st.container(key="chat_fab"):
    with st.popover(":material/smart_toy:"):
        chat_panel()
