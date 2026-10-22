"""
autocut_shibi.py — compatibilité v1 : la ligne de commande historique.

    python autocut_shibi.py --check
    python autocut_shibi.py --video "D:\\rec\\autre_video.mkv"

Pilote le même moteur que la nouvelle interface (app.py / Lancer.bat).
"""
import sys

from autoshibi.cli import main

if __name__ == "__main__":
    sys.exit(main())
