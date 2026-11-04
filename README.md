# Air+ Process Radar

Prototype du projet 3 du hackathon Capgemini × ESILV (4-6 novembre 2026) : reconstruire le processus réel
d'assemblage d'Air+ à partir des données MES, PLM et ERP, trouver les goulots, tracer une pièce d'un système
à l'autre et produire des recommandations IA qui citent leurs sources ou refusent de conclure.

## Démarrage

```bash
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python build.py                                        # chiffres clés + exports dans outputs/
streamlit run app.py                                   # http://localhost:8501
```

Le dossier `data/` doit contenir :

| Fichier | Origine |
| --- | --- |
| `MES_Extraction_FR.xlsx`, `PLM_DataSet_FR.xlsx`, `ERP_Equipes Airplus_FR.xlsx` | zip de l'organisateur |
| `ref_process_notice.csv` | transcription des 56 étapes de `meccano dreamliner.pdf` (page, section, liste de pièces) |
| `inventaire_notice.csv` | inventaire de la page 43 de la notice |
| `airplus_787.glb` | maquette 3D convertie depuis `Boeing 787 A_.stp` (voir plus bas) |

La vue 3D charge three.js depuis cdn.jsdelivr.net : il faut une connexion internet pendant la démo.

### Assistant avec un LLM (optionnel)

Sans clé, l'assistant fonctionne en mode déterministe : les phrases viennent de gabarits remplis par les calculs.
Avec une clé, un LLM reformule, et le contrôleur vérifie chaque phrase de la même façon.

```bash
export ANTHROPIC_API_KEY=...            # ou OPENAI_API_KEY=... (+ OPENAI_BASE_URL pour Mistral, Ollama, vLLM)
export AIRPLUS_LLM_MODEL=claude-sonnet-5-5
export AIRPLUS_SALT="une-phrase-secrete"   # clé de pseudonymisation ERP, hors du dépôt
```

### Regénérer la maquette 3D

Le `.gltf` fourni pointe vers un `Boeing 787 A_.bin` absent du zip : il n'a pas de géométrie. On repart du STEP :

```bash
pip install cadquery-ocp
python tools/step_to_glb.py "chemin/vers/Boeing 787 A_.stp" data/airplus_787.glb
```

## Ce que fait chaque fichier

| Fichier | Rôle |
| --- | --- |
| `etl.py` | Lecture des trois Excel, de la notice et de la maquette ; identifiants de preuve (`MES:L32`, `PLM:D145`, `NOTICE:p25-e31`) ; familles d'aléas ; pseudonymisation ERP et k-anonymat ; 9 contrôles qualité (`DQ-01` à `DQ-09`) |
| `simulate.py` | Journal d'événements : MSN-001 réel + 40 avions simulés calibrés sur MSN-001 ; révisions PLM et lots ERP fictifs |
| `mining.py` | DFG, variantes, goulots, encours, alignement et fitness, comparaison des trois versions du processus, TRS, scénario d'amélioration |
| `assistant.py` | Pipeline Analyste → Documentaliste (BM25) → Rédacteur (gabarit ou LLM) → Contrôleur |
| `viewer3d.py` | Vue three.js de la maquette avec surlignage par code pièce |
| `app.py` | Application Streamlit, huit pages |
| `build.py` | Exports CSV/JSON dans `outputs/` et questions en ligne de commande (`python build.py --ask "..."`) |

## Données : ce qui est réel, ce qui est simulé

| Réel (fichiers sources) | Simulé (étiqueté dans l'appli, colonne `source`) |
| --- | --- |
| 56 opérations de MSN-001 : temps prévus et réels, aléas, causes | 40 avions MSN-002 à MSN-041 |
| Nomenclature PLM, fournisseurs, délais, coûts | Reprises (environ 25 % des opérations, chiffre du brief) |
| 150 opérateurs ERP (pseudonymisés) | Attentes pièces sur les références critiques à long délai |
| 56 étapes de la notice, inventaire | Inversion faisceaux / lumières dans 15 % des cas |
| Arbre produit de la maquette 3D | Révisions PLM (3 ECO fictives), lots ERP, affectation des opérateurs |

Hypothèses de simulation (`SimParams` dans `simulate.py`) : chaque poste garde le ratio réel/prévu observé sur
MSN-001 avec un bruit log-normal de 8 % ; un avion est lancé toutes les 90 min (temps prévu du poste le plus long) ;
travail de 8:00 à 17:00, sept jours sur sept comme dans le MES ; une opération commencée continue le lendemain.

## Scénario de démo (5 minutes)

1. **Vue d'ensemble** : 30,8 h réelles pour 21,8 h prévues (+41 %), les 56 opérations dépassent, le poste 31 en tête.
2. **Goulots et encours** : lancer au rythme prévu fait passer le temps de cycle de 35 h à 76 h du premier au dernier avion.
3. **Fil numérique**, pièce D144 puis D145 : notice et PLM disent 1 + 1, le MES consomme deux D145, la maquette contient deux D144.
4. **Trois versions** : la séquence concorde à 100 %, l'écart est dans les temps et la matière.
5. **Assistant** : « Que recommandes-tu ? » (réponse sourcée), puis « Quel opérateur est le plus lent ? » (refus).
6. **Impact** : avec le plan, temps de cycle simulé de 57,4 h à 34,2 h, TRS simulé de 50,3 % à 62,6 %.

## Limites connues

- Un seul avion réel : toute statistique de flux (encours, variantes, files) vient de la simulation.
- Les familles d'aléas sont déduites du texte par règles ; les causes déclarées ne sont pas vérifiées.
- La notice a été transcrite à la main depuis les images : les numéros de page sont à revérifier si on la cite.
- Le TRS réel n'est pas calculable : ni qualité ni arrêts dans les données. Seule la performance (70,9 %) l'est.
