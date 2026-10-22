"""
picker.py — Sélecteur de fichier natif (Windows/macOS), lancé en sous-processus
par le serveur quand l'utilisateur clique « Parcourir… » dans l'interface web.
Affiche le chemin choisi sur stdout, rien si annulé.
"""
import sys


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "video"
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        print("", end="")
        return
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    if kind == "dir":
        path = filedialog.askdirectory(title="Dossier des projets CapCut (com.lveditor.draft)")
    else:
        path = filedialog.askopenfilename(
            title="Choisir la vidéo du rec",
            filetypes=[("Vidéos", "*.mkv *.mp4 *.mov *.avi *.webm *.flv"), ("Tous les fichiers", "*.*")])
    sys.stdout.write(path or "")


if __name__ == "__main__":
    main()
