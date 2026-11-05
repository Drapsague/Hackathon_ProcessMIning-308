"""Composant 3D réutilisable dans un dashboard Streamlit existant.

from avion_3d import afficher_avion_3d
afficher_avion_3d(hauteur=850)
"""
from pathlib import Path
import streamlit.components.v1 as components

def afficher_avion_3d(hauteur: int = 850):
    """Affiche le modèle STEP converti, sans téléchargement ni CDN à l'exécution.

    Les interactions restent internes au composant pour éviter de réinitialiser
    le modèle à chaque sélection. Elles ne sont pas renvoyées au Python parent.
    """
    html_path = Path(__file__).resolve().with_name('avion_3d.html')
    html = html_path.read_text(encoding='utf-8')
    components.html(html, height=hauteur, scrolling=False)
