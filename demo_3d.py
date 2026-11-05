"""Démonstration seule : python -m streamlit run demo_3d.py"""
import streamlit as st
from avion_3d import afficher_avion_3d

st.set_page_config(page_title='Air+ · Assemblage 3D',page_icon='✈',layout='wide',initial_sidebar_state='collapsed')
st.markdown('<style>.block-container{padding:0.6rem 1rem 0}header[data-testid="stHeader"]{background:transparent;height:0}</style>',unsafe_allow_html=True)
afficher_avion_3d(hauteur=880)
st.caption('Modèle issu du STEP fourni · Cliquez sur une pièce pour l’extraire, recliquez pour la remettre en place.')
