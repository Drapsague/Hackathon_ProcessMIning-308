"""Air+ Process Radar — application Streamlit.

    streamlit run app.py
"""
from __future__ import annotations

import re
from dataclasses import asdict

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import mining as M
from assistant import DEMO_QUESTIONS, Assistant
from etl import CAD_FILE, DATA_DIR, FAMILY_LABELS, load_all
from simulate import ECOS, SimParams, build_event_log
from viewer3d import viewer_html

st.set_page_config(page_title="Air+ Process Radar", page_icon="✈️", layout="wide")

ORANGE, GREY, BLUE = "#ea580c", "#a1a1aa", "#2563eb"


@st.cache_resource(show_spinner="Chargement des données MES, PLM, ERP…")
def load():
    ds = load_all(DATA_DIR)
    base = SimParams()
    log, trace = build_event_log(ds, base)
    better = M.improvement_params(ds, base)
    ka, kb, _, _ = M.run_scenarios(ds, base, better)
    return ds, log, trace, Assistant(ds, log, trace, (ka, kb))


@st.cache_data(show_spinner="Simulation en cours…")
def scenario(params: dict):
    ds = load()[0]
    p = SimParams(**params)
    log, _ = build_event_log(ds, p)
    sim = log[log["source"] == "simulé"]
    return M.kpis(sim), M.case_lead_times(sim)


def pct(x, nd=0):
    return "n/d" if x is None else f"{x * 100:.{nd}f} %".replace(".", ",")


def num(x, nd=1, unit=""):
    return "n/d" if x is None else f"{x:.{nd}f}".replace(".", ",") + (f" {unit}" if unit else "")


def source_badge(text: str, simulated: bool):
    color = "#fef3c7" if simulated else "#dcfce7"
    edge = "#d97706" if simulated else "#16a34a"
    st.markdown(f"<span style='background:{color};border:1px solid {edge};border-radius:6px;padding:2px 8px;"
                f"font-size:0.8rem'>{text}</span>", unsafe_allow_html=True)


ds, log, trace, assistant = load()
real = log[log["source"] == "réel"]
sim = log[log["source"] == "simulé"]

st.sidebar.title("✈️ Air+ Process Radar")
st.sidebar.caption("SWAT team · Hackathon Capgemini × ESILV 2026")
page = st.sidebar.radio("Navigation", [
    "Vue d'ensemble", "Carte du processus", "Goulots et encours", "Fil numérique",
    "Trois versions du processus", "Assistant IA", "Impact", "Qualité des données et RGPD",
])
st.sidebar.divider()
st.sidebar.markdown("**Périmètre des données**")
st.sidebar.markdown(f"- Réel : 1 avion (MSN-001), {len(real)} opérations MES\n"
                    f"- Simulé : {sim['case_id'].nunique()} avions, {len(sim)} événements")
st.sidebar.download_button("Journal d'événements (CSV)", log.drop(columns=["start_wm", "end_wm"]).to_csv(index=False),
                           "airplus_event_log.csv", "text/csv")
st.sidebar.download_button("Traçabilité pièces (CSV)", trace.to_csv(index=False), "airplus_trace.csv", "text/csv")
st.sidebar.caption(f"Assistant : mode {assistant.writer.mode}")


if page == "Vue d'ensemble":
    st.title("MSN-001 a pris 41 % de temps de plus que prévu")
    source_badge("Données réelles : MES, PLM, ERP", False)
    k = M.kpis(real)
    cost = M.overrun_cost(ds)
    c = st.columns(4)
    c[0].metric("Temps réel (somme des opérations)", num(k["lead_time_h"], 1, "h"),
                f"+{num(k['lead_time_h'] - k['planned_h'], 1)} h vs prévu", delta_color="inverse")
    c[1].metric("Temps prévu", num(k["planned_h"], 1, "h"))
    c[2].metric("Performance (prévu / réel)", pct(k["performance"], 1))
    c[3].metric("Opérations en dépassement", f"{int((ds.mes['overrun_min'] > 0).sum())} / {len(ds.mes)}")

    st.subheader("Dépassement par opération")
    d = ds.mes[["poste", "operation", "planned_min", "real_min", "overrun_min", "alea", "family", "evidence"]].copy()
    d["famille"] = d["family"].map(FAMILY_LABELS)
    top = set(d.nlargest(5, "overrun_min")["poste"])
    d["mise_en_avant"] = d["poste"].map(lambda p: "Top 5" if p in top else "Autres")
    chart = alt.Chart(d).mark_bar().encode(
        x=alt.X("poste:O", title="Poste (ordre de la gamme)"),
        y=alt.Y("overrun_min:Q", title="Minutes au-delà du prévu"),
        color=alt.Color("mise_en_avant:N", scale=alt.Scale(domain=["Top 5", "Autres"], range=[ORANGE, GREY]),
                        legend=None),
        tooltip=["poste", "operation", alt.Tooltip("planned_min", title="prévu (min)"),
                 alt.Tooltip("real_min", title="réel (min)", format=".2f"), "alea", "famille", "evidence"],
    ).properties(height=260)
    st.altair_chart(chart)
    st.caption("Le poste 31 (train gauche) perd à lui seul 37,75 min. Survolez une barre pour voir la ligne MES.")

    left, right = st.columns(2)
    with left:
        st.subheader("Minutes perdues par famille d'aléa")
        fp = M.family_pareto(ds)
        ch = alt.Chart(fp).mark_bar(color=BLUE).encode(
            y=alt.Y("label:N", sort="-x", title=None), x=alt.X("overrun_min:Q", title="minutes"),
            tooltip=["label", "operations", alt.Tooltip("overrun_min", format=".1f"), "postes"],
        ).properties(height=240)
        st.altair_chart(ch)
        st.caption("Familles déduites du texte de l'aléa déclaré (règles dans etl.py). Causes non vérifiées (DQ-08).")
    with right:
        st.subheader("Ce que la dérive coûte")
        st.metric("Main-d'œuvre directe de la dérive", f"{cost['overrun_cost_eur'].sum():.0f} €")
        st.markdown(
            "Hypothèse : un opérateur par opération, coût horaire moyen ERP du poste. Ce coût direct est faible ; "
            "le vrai coût est dans le flux : en lançant les avions au rythme prévu, l'encours explose "
            "(voir *Goulots et encours*).")
        st.markdown("**Trois constats à retenir**")
        st.markdown(
            "1. Toutes les opérations dépassent, de +28 % à +55 % : la gamme est sous-estimée partout.\n"
            "2. Le goulot réel (poste 31, 128 min) dépasse de 38 min le takt prévu (90 min).\n"
            "3. Les données elles-mêmes se contredisent : queue arrière, fins à 17:00, nomenclature.")

elif page == "Carte du processus":
    st.title("Carte du processus")
    c1, c2, c3 = st.columns(3)
    pop = c1.radio("Population", ["Flotte simulée", "MSN-001 réel"], horizontal=True)
    level = c2.radio("Niveau", ["section", "opération"], horizontal=True)
    min_count = c3.slider("Masquer les transitions vues moins de", 1, 10, 1)
    df = sim if pop == "Flotte simulée" else real
    source_badge("Simulé : 40 avions calibrés sur MSN-001" if pop == "Flotte simulée" else "Réel : MES", pop == "Flotte simulée")
    edges, gaps, loops = M.dfg(df, ds, "section" if level == "section" else "activity")
    st_tab = M.station_table(df, ds)
    if level == "section":
        heat_src = st_tab.groupby("section")["queue_total_min" if pop == "Flotte simulée" else "overrun_total_min"].sum()
    else:
        heat_src = st_tab.set_index("activity")["queue_total_min" if pop == "Flotte simulée" else "overrun_total_min"]
    heat = (heat_src / heat_src.max()).to_dict() if heat_src.max() > 0 else {}
    st.graphviz_chart(M.dfg_dot(edges, loops, heat, min_count))
    st.caption("Couleur : file d'attente (simulé) ou dépassement (réel). ↺ = reprises sur le nœud. "
               "Ellipses jaunes : attentes pièces.")
    st.subheader("Variantes")
    ign = st.toggle("Ignorer les reprises (variantes de séquence)", value=True)
    v = M.variants(df, ds, ignore_rework=ign)
    st.dataframe(v[["cases", "share", "lead_time_h", "écart vs variante principale", "exemples"]].rename(columns={
        "cases": "avions", "share": "part", "lead_time_h": "temps de cycle moyen (h)"}),
        column_config={"part": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=1)},
        hide_index=True)

elif page == "Goulots et encours":
    st.title("Le takt est calé sur le temps prévu du goulot, pas sur son temps réel")
    source_badge("Simulé : 40 avions lancés toutes les 90 min", True)
    k = M.kpis(sim)
    c = st.columns(4)
    c[0].metric("Temps de cycle moyen", num(k["lead_time_h"], 1, "h ouvrées"))
    c[1].metric("Premier → dernier avion", f"{num(k['lead_time_first_h'], 0)} → {num(k['lead_time_last_h'], 0)} h")
    c[2].metric("Encours moyen", num(k["wip_mean"], 1, "avions"))
    c[3].metric("Cadence de sortie (avions/jour)", num(k["throughput_per_day"], 2))
    lt = M.case_lead_times(sim)
    lt["n"] = lt["case_id"].str[-3:].astype(int)
    left, right = st.columns(2)
    with left:
        st.subheader("Temps de cycle par avion")
        st.altair_chart(alt.Chart(lt).mark_line(point=True, color=ORANGE).encode(
            x=alt.X("n:Q", title="Numéro d'avion (MSN)"), y=alt.Y("lead_time_h:Q", title="heures ouvrées"),
            tooltip=["case_id", alt.Tooltip("lead_time_h", format=".1f"), alt.Tooltip("queue_h", format=".1f")],
        ).properties(height=260))
        st.caption("Chaque avion attend un peu plus que le précédent devant le poste 31 : la file ne se résorbe jamais.")
    with right:
        st.subheader("Encours dans le temps")
        w = M.wip_series(sim)
        st.altair_chart(alt.Chart(w).mark_line(interpolate="step-after", color=BLUE).encode(
            x=alt.X("t:T", title=None), y=alt.Y("wip:Q", title="avions en cours")).properties(height=260))
    st.subheader("Postes classés par file d'attente")
    t = M.station_table(sim, ds).sort_values("queue_total_min", ascending=False)
    t["queue_h"] = t["queue_total_min"] / 60
    st.dataframe(t[["poste", "activity", "planned_min", "real_min", "overrun_pct", "queue_h", "reworks", "utilisation",
                    "family"]].rename(columns={
        "activity": "opération", "planned_min": "prévu (min)", "real_min": "réel moyen (min)",
        "overrun_pct": "écart %", "queue_h": "file cumulée (h)", "reworks": "reprises", "family": "famille"}),
        column_config={"utilisation": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
                       "écart %": st.column_config.NumberColumn(format="%.0f"),
                       "file cumulée (h)": st.column_config.NumberColumn(format="%.0f"),
                       "réel moyen (min)": st.column_config.NumberColumn(format="%.1f")},
        hide_index=True, height=360)

elif page == "Fil numérique":
    st.title("Fil numérique : PLM → MES → ERP → maquette 3D")
    codes = ds.plm["code"].tolist()
    c1, c2 = st.columns([1, 1])
    mode = c1.radio("Tracer", ["une pièce", "un poste"], horizontal=True)
    if mode == "une pièce":
        code = c2.selectbox("Référence", codes, index=codes.index("D144"),
                            format_func=lambda c: f"{c} · {ds.plm.set_index('code').loc[c, 'designation']}")
        highlight = [code]
    else:
        poste = c2.selectbox("Poste", ds.mes["poste"].tolist(), index=30,
                             format_func=lambda p: ds.mes.set_index("poste").loc[p, "activity"])
        highlight = sorted(set(ds.mes.set_index("poste").loc[poste, "refs"]))
        code = None

    left, right = st.columns([1, 1.25])
    with left:
        if code:
            p = ds.plm.set_index("code").loc[code]
            b = ds.bom.set_index("code").loc[code]
            st.markdown(f"#### {code} · {p['designation']}")
            source_badge(f"PLM ligne {p['plm_line']}", False)
            st.markdown(f"Fournisseur **{p['supplier']}**, délai {p['lead_time']}, criticité **{p['criticality']}**, "
                        f"{p['unit_cost_eur']:.0f} € pièce, {p['cad_hours']:.0f} h de CAO.")
            cmp = pd.DataFrame({"source": ["Notice (p. 43)", "PLM", "MES (MSN-001)", "Maquette 3D"],
                                "quantité": [b["notice"], b["plm"], b["mes"], b["cad"]]})
            st.dataframe(cmp, hide_index=True)
            rel = [f for f in ds.findings if code in f.detail]
            for f in rel:
                st.warning(f"**{f.id} · {f.title}** — {f.detail}")
            uses = ds.mes[ds.mes["refs"].map(lambda r: code in r)]
            st.markdown("**Opérations MES qui la consomment (réel)**")
            st.dataframe(uses[["poste", "operation", "start", "real_min", "alea", "evidence"]], hide_index=True)
        else:
            r = ds.mes.set_index("poste").loc[poste]
            st.markdown(f"#### Poste {poste} · {r['operation']}")
            source_badge(r["evidence"], False)
            st.markdown(f"Prévu {r['planned_min']:.0f} min, réel {r['real_min']:.2f} min, aléa « {r['alea']} » "
                        f"(cause déclarée : {r['cause']}).")
            pl = ds.plm[ds.plm["code"].isin(highlight)][["code", "designation", "supplier", "criticality", "lead_time"]]
            st.dataframe(pl, hide_index=True)
            ops = ds.erp[ds.erp["poste"] == poste][["operator_id", "level", "certifications", "hourly_cost_eur"]]
            st.markdown("**Opérateurs affectés (ERP pseudonymisé)**")
            st.dataframe(ops, hide_index=True)
    with right:
        glb = DATA_DIR / CAD_FILE
        if glb.exists():
            components.html(viewer_html(glb, highlight), height=480)
            st.caption("Glisser pour tourner, molette pour zoomer. Maquette convertie depuis le STEP CATIA.")
        else:
            st.info("Maquette absente : lancer `python tools/step_to_glb.py` (voir README).")

    st.subheader("Traçabilité par avion")
    source_badge("Révisions PLM et lots ERP simulés : absents des données sources", True)
    t = trace[trace["code"].isin(highlight)] if code else trace[trace["poste"] == poste]
    st.dataframe(t[["case_id", "poste", "activity", "code", "qty", "plm_rev", "erp_lot", "start", "source", "evidence"]],
                 hide_index=True, height=260)
    eco = [e for e in ECOS if e["code"] in highlight]
    if eco:
        st.markdown("**Modifications d'ingénierie (fictives)**")
        st.dataframe(pd.DataFrame(eco), hide_index=True)

elif page == "Trois versions du processus":
    st.title("Documenté, outil, observé : la séquence concorde, les temps et la matière non")
    tv = M.three_versions(ds)
    c = st.columns(3)
    c[0].metric("Étapes notice / opérations MES", f"{len(ds.notice_steps)} / {len(ds.mes)}")
    cf = M.conformance(log, ds)
    c[1].metric("Fitness d'ordre MSN-001 vs gamme", pct(cf.loc[cf["case_id"] == "MSN-001", "fitness_ordre"].iat[0]))
    c[2].metric("Fitness moyenne flotte simulée", pct(cf.loc[cf["source"] == "simulé", "fitness_ordre"].mean(), 1))
    st.markdown("**Documenté** : notice Meccano, 56 étapes transcrites page par page. **Outil** : gamme MES, ordre "
                "des postes et temps prévus, nomenclature PLM. **Observé** : temps réels et aléas de MSN-001.")
    sections = ["Toutes"] + list(dict.fromkeys(tv["notice : section"]))
    sel = st.selectbox("Section", sections)
    view = tv if sel == "Toutes" else tv[tv["notice : section"] == sel]
    st.dataframe(view, hide_index=True, height=380,
                 column_config={"observé : écart (%)": st.column_config.NumberColumn(format="+%.0f %%")})
    st.subheader("Nomenclature : notice, PLM, MES, maquette 3D")
    bom = ds.bom.copy()
    bom["statut"] = bom.apply(lambda r: "écart" if (pd.notna(r["plm"]) and r["mes"] != r["plm"]) or pd.isna(r["notice"])
                              or pd.isna(r["plm"]) else "ok", axis=1)
    only = st.toggle("Afficher seulement les écarts", value=True)
    st.dataframe(bom[bom["statut"] == "écart"] if only else bom, hide_index=True,
                 column_config={"cad": st.column_config.NumberColumn("maquette 3D"),
                                "mes_vs_plm": st.column_config.NumberColumn("MES - PLM")})
    st.caption("La visserie n'est pas modélisée dans la maquette 3D : un 0 en CAO n'y est pas une anomalie.")
    st.subheader("Conformité par avion (simulé)")
    st.dataframe(cf, hide_index=True, height=240,
                 column_config={"fitness_ordre": st.column_config.NumberColumn(format="%.3f"),
                                "fitness_avec_ajouts": st.column_config.NumberColumn(format="%.3f"),
                                "temps_reel_vs_prevu": st.column_config.NumberColumn(format="%.2f")})

elif page == "Assistant IA":
    st.title("Assistant à preuves")
    st.markdown("Chaque phrase cite une ligne de log, une page de la notice ou un constat qualité. "
                "Sans preuve, l'assistant répond **DONNÉES INSUFFISANTES**.")
    st.caption(f"Rédacteur : {assistant.writer.mode}. Pour un LLM, définir ANTHROPIC_API_KEY ou OPENAI_API_KEY "
               "avant de lancer l'application.")
    if "history" not in st.session_state:
        st.session_state.history = []
    cols = st.columns(3)
    for i, q in enumerate(DEMO_QUESTIONS):
        if cols[i % 3].button(q, key=f"demo{i}"):
            st.session_state.pending = q
    q = st.chat_input("Posez une question sur la production Air+")
    q = q or st.session_state.pop("pending", None)
    if q:
        st.session_state.history.insert(0, assistant.ask(q))
    for a in st.session_state.history[:6]:
        with st.chat_message("user"):
            st.write(a.question)
        with st.chat_message("assistant"):
            if a.refused:
                st.error(a.text)
            else:
                for s in a.sentences:
                    if s.ok:
                        body = re.sub(r"\s*\[[^\]]+\]", "", s.text).strip()
                        chips = " ".join(f"`{i}`" for i in s.ids)
                        st.markdown(f"{body} {chips}")
            with st.expander(f"Preuves citées ({len(a.evidence)})"):
                for e in a.evidence.values():
                    tag = "🟡 simulé" if e.simulated else "🟢 réel"
                    st.markdown(f"`{e.id}` · {e.source} · {tag}  \n{e.text}")
            with st.expander("Pipeline et contrôle"):
                st.table(pd.DataFrame(a.steps, columns=["agent", "action"]))
                if a.sentences:
                    st.dataframe(pd.DataFrame([{"phrase": s.text[:120], "citations": ", ".join(s.ids),
                                                "validée": s.ok, "problèmes": "; ".join(s.issues)}
                                               for s in a.sentences]), hide_index=True)

elif page == "Impact":
    st.title("Impact du plan d'action, simulé sur 40 avions")
    source_badge("Simulation : mêmes tirages aléatoires avant et après", True)
    c1, c2, c3 = st.columns(3)
    top_k = c1.slider("Postes dont on traite l'aléa (classés par minutes perdues)", 0, 10, 5)
    fixed = c1.slider("Ratio réel / prévu visé sur ces postes", 1.0, 1.4, 1.10, 0.05)
    rw = c2.slider("Réduction des reprises", 0, 80, 40, 5, format="%d %%")
    order = c2.checkbox("Imposer faisceaux avant lumières (détrompage MES)", True)
    parts = c3.checkbox("Sécuriser le bord de ligne des pièces critiques", True)
    takt = c3.checkbox("Recaler le takt sur le temps réel du goulot", True)
    base = SimParams()
    better = M.improvement_params(ds, base, top_k, fixed, 1 - rw / 100, order, parts, takt)
    ka, lta = scenario(asdict(base))
    kb, ltb = scenario(asdict(better))
    m = st.columns(5)
    m[0].metric("Temps de cycle moyen", num(kb["lead_time_h"], 1, "h"), f"{num(kb['lead_time_h'] - ka['lead_time_h'], 1)} h",
                delta_color="inverse")
    m[1].metric("Taux de reprise", pct(kb["rework_rate"]), f"{(kb['rework_rate'] - ka['rework_rate']) * 100:+.0f} pts",
                delta_color="inverse")
    m[2].metric("TRS simulé", pct(kb["oee"], 1), f"{(kb['oee'] - ka['oee']) * 100:+.1f} pts")
    m[3].metric("Encours moyen", num(kb["wip_mean"], 1, "avions"), f"{kb['wip_mean'] - ka['wip_mean']:+.1f}",
                delta_color="inverse")
    m[4].metric("Cadence (avions/jour)", num(kb["throughput_per_day"], 2), f"{kb['throughput_per_day'] - ka['throughput_per_day']:+.2f}")
    lta["scénario"], ltb["scénario"] = "Aujourd'hui", "Avec le plan"
    both = pd.concat([lta, ltb])
    both["n"] = both["case_id"].str[-3:].astype(int)
    st.altair_chart(alt.Chart(both).mark_line(point=True).encode(
        x=alt.X("n:Q", title="Numéro d'avion (MSN)"), y=alt.Y("lead_time_h:Q", title="temps de cycle (h ouvrées)"),
        color=alt.Color("scénario:N", scale=alt.Scale(domain=["Aujourd'hui", "Avec le plan"], range=[GREY, ORANGE])),
    ).properties(height=280))
    rows = [("Temps de cycle moyen (h)", ka["lead_time_h"], kb["lead_time_h"]),
            ("Taux de reprise", ka["rework_rate"], kb["rework_rate"]),
            ("Disponibilité", ka["availability"], kb["availability"]),
            ("Performance", ka["performance"], kb["performance"]),
            ("Qualité", ka["quality"], kb["quality"]), ("TRS", ka["oee"], kb["oee"]),
            ("Encours moyen (avions)", ka["wip_mean"], kb["wip_mean"]),
            ("Cadence (avions/jour)", ka["throughput_per_day"], kb["throughput_per_day"])]
    st.dataframe(pd.DataFrame(rows, columns=["indicateur", "aujourd'hui", "avec le plan"]), hide_index=True,
                 column_config={"aujourd'hui": st.column_config.NumberColumn(format="%.3f"),
                                "avec le plan": st.column_config.NumberColumn(format="%.3f")})
    st.caption(f"Postes traités : {', '.join(map(str, better.fixed_postes)) or 'aucun'} · takt "
               f"{num(better.takt_min or 90, 0)} min. TRS = disponibilité (hors attentes pièces) × performance "
               "(prévu / réel) × qualité (opérations sans reprise).")

else:
    st.title("Qualité des données et protection des données personnelles")
    st.subheader("Constats sur les fichiers sources")
    sev = {"haute": "🔴", "moyenne": "🟠", "info": "🔵"}
    for f in ds.findings:
        with st.expander(f"{sev[f.severity]} {f.id} · {f.title}"):
            st.write(f.detail)
            st.caption("Preuves : " + ", ".join(f.evidence))
    st.subheader("ERP : pseudonymisation dès la lecture")
    c = st.columns(3)
    c[0].metric("k-anonymat brut (poste, âge, qualification, niveau)", f"k = {ds.kanon_full['k']}")
    c[1].metric("Lignes uniques sur ces attributs", pct(ds.kanon_full["unique_share"]))
    c[2].metric("k-anonymat des agrégats par poste envoyés à l'IA", f"k = {ds.kanon_released['k']}")
    st.markdown(
        "- Supprimés à la lecture : prénom, nom, commentaire de carrière (identifiants directs et texte libre).\n"
        "- Matricule remplacé par un HMAC-SHA256 tronqué (`OP-xxxxxx`), clé `AIRPLUS_SALT` hors du dépôt : "
        "c'est une **pseudonymisation**, les données restent personnelles au sens du RGPD.\n"
        "- Âge passé en tranche de 10 ans. Même ainsi, 92 % des lignes restent uniques : on n'envoie donc au LLM "
        "que des **agrégats par poste** (effectif, coût moyen, niveaux), jamais une ligne individuelle.\n"
        "- L'assistant refuse toute question sur l'identité ou la performance d'une personne.")
    st.dataframe(ds.erp.drop(columns=["skills", "rotation"]).head(12), hide_index=True)
