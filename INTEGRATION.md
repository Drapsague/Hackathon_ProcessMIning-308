# Intégration dans le dashboard de votre équipe

## Les deux fichiers nécessaires

Copier `avion_3d.py` et `avion_3d.html` côte à côte dans le dossier Python de votre dashboard. Le HTML contient déjà les maillages, les données liées et les bibliothèques Three.js/OrbitControls. Aucun CDN ni fichier .bin à télécharger pour l'affichage. L'accès Internet reste nécessaire pour installer Streamlit la première fois.

## Ajouter la visualisation à une page Streamlit

```python
from avion_3d import afficher_avion_3d

# À placer dans l'onglet ou la page dédiée à l'avion.
afficher_avion_3d(hauteur=850)
```

Si vous utilisez des onglets :

```python
import streamlit as st
from avion_3d import afficher_avion_3d

analyse, avion = st.tabs(['Analyse', 'Avion 3D'])
with analyse:
    st.write('Vos graphiques habituels')
with avion:
    afficher_avion_3d(hauteur=850)
```

Ne pas copier `st.set_page_config` de la démo dans votre dashboard s'il en contient déjà un. Un seul appel de configuration dans l'application.

## Tester uniquement la partie 3D

Dans Terminal, depuis ce dossier :

```bash
python -m pip install -r requirements.txt
python -m streamlit run demo_3d.py --server.address 127.0.0.1
```

Pour ouvrir le dashboard précédent avec la page 3D ajoutée :

```bash
python -m streamlit run app.py --server.address 127.0.0.1
```

Lancer l'application, puis choisir « Avion 3D » dans le menu de gauche. Si `python` est introuvable, utiliser votre Python Anaconda, par exemple `/opt/anaconda3/bin/python`.

Le fichier `avion_3d.html` est également autonome et peut s'ouvrir directement dans un navigateur prenant en charge WebGL. La version Streamlit est la version d'intégration prévue. Préférer Chrome ou Safari récent et une fenêtre de bureau pour la démonstration.

## Fonctionnement

- Glisser dans la scène : tourner autour du modèle.
- Molette/pincement : zoomer. Clic droit : déplacer le cadrage.
- Survoler une pièce : nom et référence si identifiée, avec mise en évidence.
- Cliquer sur une pièce : extraction animée, informations à droite.
- Recliquer sur cette pièce ou « Remettre cette pièce » : retour animé à sa position d'origine.
- Plusieurs pièces peuvent rester extraites. Les boutons des pièces extraites permettent de les remettre individuellement.
- « Réassembler » ou Échap : retour de toutes les pièces et annulation de la vue éclatée.
- Curseur « Vue éclatée » : séparation progressive de toutes les pièces.
- Transparence : rend le reste de l'avion translucide pour voir les composants internes.
- Recherche et filtre de sous-assemblage : masquent les pièces hors périmètre.
- « Cadrer » : ajuste le cadrage au modèle visible. « Cadrer cette pièce » : se concentre sur une sélection.
- Rotation auto, plein écran (si autorisé par le navigateur) et export PNG.

## Origine et limites

La géométrie provient du STEP « Boeing 787 A_.stp ». Les 99 formes sont tessellées séparément, avec application de leurs transformations d'assemblage. L'affichage applique uniquement une rotation d'axes, un centrage et une échelle uniforme. L'extraction et le réassemblage sont des animations de présentation, sans simulation physique des collisions ni validation d'une procédure de démontage.

Les références sont extraites uniquement des noms d'instances CAO, jamais déduites de la forme. Les noms génériques restent visibles comme « référence non identifiée ». Un code peut correspondre à plusieurs instances. Une pièce CAO peut ne pas avoir de référence ou ne pas avoir d'opération MES associée.

Mode Production : le code de référence relie la pièce aux opérations MES. La couleur représente le maximum de l'écart relatif `(réel déclaré - prévu) / prévu` des opérations liées : vert ≤25 %, orange >25 à 40 %, rouge >40 %. Gris : aucune opération reliée. Ce sont des seuils de lecture choisis pour la démonstration, pas des seuils de conformité industrielle. Le maximum sur les opérations liées ne mesure pas un défaut de la pièce ni sa contribution causale au retard. Les horaires des postes 15,31,50 restent signalés comme incohérents.

Mode Criticité : attribut Criticité du PLM fourni. Les fiches affichent les sources, les coûts/délais de référence et les opérations liées. Les données sont un instantané de votre jeu de fichiers, pas une connexion temps réel aux systèmes.

Les clics restent à l'intérieur du composant HTML pour que les animations soient fluides. Ils ne mettent pas à jour les autres tableaux Python du dashboard. En revanche, le panneau métier interne suit la pièce sélectionnée.

Une interaction dans un autre widget Streamlit peut réexécuter la page et réinitialiser le composant. Garder la visualisation sur une page ou un onglet dédié avec ses commandes à l'intérieur.

## Pour votre oral

1. Montrer l'avion assemblé et le faire tourner.
2. Cliquer sur une aile ou la retrouver dans la liste.
3. Extraire une deuxième pièce pour montrer la sélection multiple.
4. Remettre les pièces individuellement, puis montrer la vue éclatée.
5. Activer le mode Production et expliquer le lien par référence avec le MES.
6. Terminer par « Réassembler » et rappeler que la géométrie correspond aux données CAO fournies.

La distinction repose sur la fluidité de l'exploration et la traçabilité des informations. Le jury reste souverain pour la note.

## Licence

Three.js et OrbitControls sont distribués sous licence MIT, incluse dans LICENSE_three.txt. Géométrie et données métier : fichiers fournis pour le hackathon. Aucune image d'avion générée n'est utilisée.
