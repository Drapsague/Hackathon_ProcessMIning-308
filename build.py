"""Construit les sorties à partir des fichiers de data/ et affiche les chiffres clés.

    python build.py            -> outputs/*.csv + résumé
    python build.py --ask "Où sont les goulots ?"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import mining as M
from assistant import Assistant
from etl import load_all
from simulate import SimParams, build_event_log

OUT = Path(__file__).parent / "outputs"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", help="poser une question à l'assistant")
    ap.add_argument("--aircraft", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    ds = load_all()
    base = SimParams(n_aircraft=args.aircraft, seed=args.seed)
    log, trace = build_event_log(ds, base)
    real, sim = log[log["source"] == "réel"], log[log["source"] == "simulé"]
    better = M.improvement_params(ds, base)
    ka, kb, _, _ = M.run_scenarios(ds, base, better)

    if args.ask:
        a = Assistant(ds, log, trace, (ka, kb)).ask(args.ask)
        print(a.text)
        for s in a.sentences:
            if not s.ok:
                print("  rejeté :", "; ".join(s.issues), "|", s.text[:90])
        return

    OUT.mkdir(exist_ok=True)
    log.drop(columns=["start_wm", "end_wm"]).to_csv(OUT / "event_log.csv", index=False)
    trace.to_csv(OUT / "trace_pieces.csv", index=False)
    ds.bom.to_csv(OUT / "nomenclature_4_sources.csv", index=False)
    M.three_versions(ds).to_csv(OUT / "trois_versions.csv", index=False)
    M.station_table(sim, ds).to_csv(OUT / "postes_simulation.csv", index=False)
    ds.erp.drop(columns=["skills"]).to_csv(OUT / "erp.csv", index=False)
    with open(OUT / "constats_qualite.json", "w", encoding="utf-8") as f:
        json.dump([vars(x) for x in ds.findings], f, ensure_ascii=False, indent=2)
    with open(OUT / "kpi.json", "w", encoding="utf-8") as f:
        json.dump({"reel_msn001": M.kpis(real), "simule_aujourdhui": ka, "simule_plan": kb,
                   "postes_traites": list(better.fixed_postes), "takt_plan_min": better.takt_min}, f, indent=2)

    kr = M.kpis(real)
    print(f"MSN-001 : {kr['lead_time_h']:.1f} h réelles / {kr['planned_h']:.1f} h prévues "
          f"(+{(kr['lead_time_h'] / kr['planned_h'] - 1) * 100:.0f} %), performance {kr['performance']:.1%}")
    print(f"Simulé ({ka['cases']} avions) : cycle {ka['lead_time_h']:.1f} h, reprise {ka['rework_rate']:.0%}, "
          f"TRS {ka['oee']:.1%}, encours {ka['wip_mean']:.1f}")
    print(f"Avec le plan : cycle {kb['lead_time_h']:.1f} h, reprise {kb['rework_rate']:.0%}, "
          f"TRS {kb['oee']:.1%}, encours {kb['wip_mean']:.1f}")
    print(f"{len(ds.findings)} constats qualité, k-anonymat ERP brut = {ds.kanon_full['k']}")
    print(f"Sorties écrites dans {OUT}/")


if __name__ == "__main__":
    main()
