# Air+ | Industrial Performance Radar

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
| `MES_Extraction_UK.xlsx`, `PLM_DataSet_UK.xlsx`, `ERP_Equipes_Airplus_UK.xlsx` | versions anglaises de l'organisateur, lues en priorité |
| `MES_Extraction_FR.xlsx`, `PLM_DataSet_FR.xlsx`, `ERP_Equipes Airplus_FR.xlsx` | versions françaises, utilisées si les UK sont absentes |
| `ref_process_notice.csv` | transcription des 56 étapes de `meccano dreamliner.pdf` (page, section, liste de pièces) |
| `inventaire_notice.csv` | inventaire de la page 43 de la notice |
| `airplus_787.glb` | maquette 3D convertie depuis `Boeing 787 A_.stp` (voir plus bas) |

La vue 3D charge three.js depuis cdn.jsdelivr.net : il faut une connexion internet pendant la démo.

### Assistant avec un LLM (optionnel)

Sans clé, l'assistant fonctionne en mode déterministe : les phrases viennent de gabarits remplis par les calculs.
Avec une clé, un LLM reformule et peut ajouter des hypothèses signalées « Hypothèse : » (sans chiffre) ; le
contrôleur vérifie chaque phrase de la même façon.

Créer un fichier `.env` à la racine (jamais commité) :

```
GROQ_API_KEY=gsk_...
AIRPLUS_LLM_MODEL=openai/gpt-oss-120b
```

`ANTHROPIC_API_KEY` ou `OPENAI_API_KEY` (+ `OPENAI_BASE_URL`) fonctionnent aussi. Après une modification du `.env`
ou des modules Python, redémarrer Streamlit.

### Regénérer la maquette 3D

Le `.gltf` fourni pointe vers un `Boeing 787 A_.bin` absent du zip : il n'a pas de géométrie. On repart du STEP :

```bash
pip install cadquery-ocp
python tools/step_to_glb.py "chemin/vers/Boeing 787 A_.stp" data/airplus_787.glb
```

## Les sept onglets et l'assistant

L'interface, les PDF et les réponses de l'assistant sont en anglais ; l'assistant comprend aussi les
questions en français. Avec les fichiers UK, les libellés d'opérations, aléas et
désignations sont aussi en anglais ; FR et UK donnent exactement les mêmes chiffres.


| Onglet | Contenu |
| --- | --- |
| Overview | 4 indicateurs (opérations en retard, dépassement cumulé, priorités élevées P04, coût total des pièces FIN01), top 5 des postes par dépassement, répartition des priorités, bouton « Résumé des anomalies » |
| Production | Les 8 KPI MES, gravité des retards, opérations à examiner en priorité, filtres poste / date / gravité / incident, tableau des opérations avec les matricules du poste, incidents déclarés |
| PLM parts | Pièces critiques, délais > Q3, opérations concernées, tableau des pièces, vue 3D d'une pièce |
| ERP teams | Effectifs par poste, niveaux d'expérience, matricules, détail d'un poste au clic |
| Financial analysis | FIN01 à FIN05 : coût total des pièces, coût par référence et part cumulée, coût horaire théorique par poste, coût moyen d'une ressource |
| Simulation | Atelier d'assemblage 3D interactif (extraction de pièces, vue éclatée, modes Production et Criticité) et rejeu de la chaîne MSN-001 |
| Glossary | Définitions des termes, règles et indicateurs, avec recherche |

Chaque onglet s'exporte en PDF (bouton en haut à droite, graphiques matplotlib), le résumé des anomalies aussi.
L'assistant IA est le robot flottant en bas à droite, accessible depuis tous les onglets. Les coûts affichés sont
des coûts de référence (PLM, ERP), jamais des pertes attribuées aux retards.

## Ce que fait chaque fichier

| Fichier | Rôle |
| --- | --- |
| `rules.py` | Règles du cahier : MES01-09, PLM01-06, ERP01-07, CROSS01-06, score P04, FIN01-05, KPI, anomalies et couleurs |
| `etl.py` | Lecture des Excel, de la notice et de la maquette ; identifiants de preuve ; ERP sans noms ni commentaires ; contrôles de cohérence entre sources (`DQ-01` à `DQ-09`) |
| `assistant.py` | Analyste → Documentaliste (BM25) → Rédacteur (gabarit ou LLM) → Contrôleur, sur données réelles uniquement |
| `mining.py`, `simulate.py` | Indicateurs MSN-001 utilisés par l'assistant ; la flotte simulée n'est plus affichée |
| `viewer3d.py` | Vue three.js de la maquette : surlignage d'une pièce, et simulation de la chaîne (opérations rejouées dans l'ordre et selon leurs durées réelles) |
| `report.py` | Exports PDF de chaque onglet et du résumé des anomalies (fpdf2 + matplotlib) |
| `app.py` | Application Streamlit, sept onglets et l'assistant flottant |
| `avion_3d.py`, `avion_3d.html` | Atelier 3D autonome (Three.js embarqué, licence dans `LICENSE_three.txt`) |
| `glossaire.py` | Termes de l'onglet Glossaire |
| `.streamlit/config.toml` | Thème clair (fond blanc) |
| `build.py` | Exports CSV/JSON dans `outputs/` et questions en ligne de commande |

Choix ouverts du cahier, réglables en tête de `rules.py` : Q3 calculé sur la borne haute des fourchettes de délai
(`DELAI_RETENU`) et tolérance horaire MES05 de 1 minute (`TOLERANCE_HORAIRE_MIN`). Une opération sans référence
pièce (poste 51) a un score provisoire : les critères PLM ne sont pas évaluables.

## Scénario de démo (5 minutes)

1. **Vue globale** : 56 opérations en retard sur 56, 537,5 min de dépassement, 20 priorités élevées, 21 099 590 € de pièces.
2. **Résumé des anomalies** : scores, signaux CROSS, fins tronquées à 17:00 (MES05), puis export PDF.
3. **Production** : filtrer « critique », lire les aléas déclarés, rejouer la ligne en 3D.
4. **Pièces PLM** : délais au-dessus du Q3, puis D144 dans la maquette 3D.
5. **Analyse financière** : D142 pèse 47 % du coût des pièces ; coûts de référence, pas des pertes.
6. **Simulation** : charger l'atelier 3D, extraire une aile, passer en mode Production.
7. **Assistant** : « Which operations should be investigated first? », puis « Who was late on part A623? ».

## Limites connues

- Un seul avion réel : l'encours, les variantes et le TRS complet ne sont pas mesurables.
- Les familles d'aléas sont déduites du texte par règles ; les causes déclarées ne sont pas vérifiées.
- La notice a été transcrite à la main depuis les images : les numéros de page sont à revérifier si on la cite.
- Le TRS réel n'est pas calculable : ni qualité ni arrêts dans les données. Seule la performance (70,9 %) l'est.
