"""Convertit le STEP CATIA en .glb autonome (géométrie embarquée, noms des pièces conservés).

Le .gltf livré par l'organisateur référence un fichier .bin absent du zip : sans lui, aucune
géométrie. On repart donc du STEP, avec Open CASCADE (le même moteur qui a produit le .gltf).

    pip install cadquery-ocp
    python tools/step_to_glb.py "data/Boeing 787 A_.stp" data/airplus_787.glb
"""
import sys

from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IFSelect import IFSelect_RetDone
from OCP.Message import Message_ProgressRange
from OCP.RWGltf import RWGltf_CafWriter
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.collections import IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString as FileInfo
from OCP.collections import Sequence_TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFDoc import XCAFDoc_DocumentTool


def convert(src, dst, deflection=0.5):
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    if reader.ReadFile(src) != IFSelect_RetDone:
        raise SystemExit(f"Lecture STEP impossible : {src}")
    reader.Transfer(doc)

    shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    labels = Sequence_TDF_Label()
    shapes.GetFreeShapes(labels)
    for i in range(1, labels.Length() + 1):
        BRepMesh_IncrementalMesh(shapes.GetShape_s(labels.Value(i)), deflection, False, 0.5, True)

    writer = RWGltf_CafWriter(TCollection_AsciiString(dst), True)  # True = binaire (.glb)
    ok = writer.Perform(doc, FileInfo(), Message_ProgressRange())
    if not ok:
        raise SystemExit("Écriture glTF échouée")
    print(f"OK -> {dst}")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.5)
