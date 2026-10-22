"""
Angora Studio — point d'entrée de l'interface.

Double-clique sur Lancer.bat (ou : python app.py).
Un petit serveur local démarre sur ton PC et l'interface s'ouvre dans ton
navigateur. Tout est local : rien n'est envoyé sur internet, sauf les
tranches audio analysées par Gemini.

Options : --port N · --no-browser · --demo (aperçu sans CapCut ni clé)
Ligne de commande (comme avant) : python autocut_shibi.py --check
"""
import os
import sys
import socket
import argparse
import threading
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)


def _free_port(start):
    for port in range(start, start + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Aucun port libre trouvé.")


def main():
    ap = argparse.ArgumentParser(description="Angora Studio")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default=os.getenv("AUTOSHIBI_HOST", "127.0.0.1"))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--demo", action="store_true",
                    help="aperçu de l'interface avec des données factices (aucun fichier touché)")
    args = ap.parse_args()

    from autoshibi import server as srv
    if args.demo or os.getenv("AUTOSHIBI_DEMO") == "1":
        srv.DEMO = True

    port = _free_port(args.port) if args.host == "127.0.0.1" else args.port
    httpd = srv.serve(args.host, port)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 58)
    print("  Angora Studio" + ("  — MODE DÉMO" if srv.DEMO else ""))
    print(f"  Interface : {url}")
    print("  Ferme cette fenêtre (ou Ctrl+C) pour arrêter le logiciel.")
    print("=" * 58, flush=True)
    if not args.no_browser:
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt. À bientôt !")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        msg = traceback.format_exc()
        try:
            with open(os.path.join(BASE_DIR, "autoshibi.log"), "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass
        print(msg)
        input("Le logiciel n'a pas pu démarrer (détail ci-dessus et dans autoshibi.log). Entrée pour fermer.")
        sys.exit(1)
