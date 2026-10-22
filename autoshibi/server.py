"""
server.py — Serveur web local (bibliothèque standard uniquement).

L'interface vit dans le navigateur de l'utilisateur, le moteur reste en
Python sur sa machine : aucun envoi de fichiers, tout est local. La
progression arrive en direct par SSE (Server-Sent Events) ; fermer l'onglet
n'arrête pas un montage en cours, le rouvrir ré-affiche l'état exact.
"""
import os
import json
import time
import queue
import shutil
import threading
import subprocess
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import config, media, capcut, projedit, checks, pipeline as pipe
from .events import Reporter, CancelToken
from . import __version__

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
VIDEO_EXTS = {".mkv", ".mp4", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts"}
DEMO = False                       # activé par app.py --demo

_STATE = {"reporter": None, "cancel": None, "thread": None, "job": None}
_LOCK = threading.Lock()


def _demo():
    from . import demo
    return demo


# ------------------------------------------------------------------ helpers métier
def bootstrap():
    job = config.load_settings()
    if DEMO:
        demo = _demo()
        return {"version": __version__, "demo": True, "job": job,
                "api_key_present": True, "api_key_hint": "••••démo",
                "projects_dir": "C:/Users/Demo/AppData/Local/CapCut/User Data/Projects/com.lveditor.draft",
                "projects": [{"name": p["name"], "modified": p["modified"]} for p in demo.PROJECTS],
                "ffmpeg": True, "ffprobe": True,
                "default_video": demo.VIDEO_INFO["path"], "base_dir": config.BASE_DIR,
                "models": config.gemini_models(), "capcut_running": False}
    pdir = config.find_projects_dir(job.get("projects_dir") or None)
    key = config.gemini_api_key()
    default_video = ""
    cand = os.path.join(config.BASE_DIR, "video.mkv")
    if os.path.exists(cand):
        default_video = cand
    return {"version": __version__, "demo": False, "job": job,
            "api_key_present": bool(key), "api_key_hint": ("••••" + key[-4:]) if key else "",
            "projects_dir": pdir or "", "projects": _projects_with_mtime(pdir),
            "ffmpeg": media.have("ffmpeg"), "ffprobe": media.have("ffprobe"),
            "default_video": default_video, "base_dir": config.BASE_DIR,
            "models": config.gemini_models(), "capcut_running": _capcut_running()}


def _capcut_running():
    try:
        from . import pipeline as _p
        return _p.capcut_running()
    except Exception:
        return False


def _projects_with_mtime(pdir):
    out = []
    for name in capcut.list_projects(pdir):
        files = capcut.draft_files(os.path.join(pdir, name))
        try:
            mt = max(os.path.getmtime(f) for f in files)
        except OSError:
            mt = 0
        out.append({"name": name, "modified": mt})
    return out


def project_info(name, pdir):
    if DEMO:
        for p in _demo().PROJECTS:
            if p["name"] == name:
                return {"ok": True, "info": p["info"]}
        return {"ok": False, "error": "Projet inconnu (démo)"}
    full = os.path.join(pdir, name)
    files = capcut.draft_files(full)
    if not files:
        return {"ok": False, "error": "Aucun draft_content.json (ouvre le projet dans CapCut puis ferme CapCut)"}
    try:
        newest = max(files, key=os.path.getmtime)
        info = projedit.summarize(capcut.load_draft(newest))
        info["canvas"] = list(info["canvas"])
        return {"ok": True, "info": info}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def preflight(job):
    job = config.coerce_job(job)
    if DEMO:
        return _demo().preflight(job)
    if job["mode"] == "edit":
        return checks.check_edit(job, full=False)
    return checks.check_generate(job, pipe.resolve_video(job), full=False)


def estimate(job):
    """Estimation honnête et instantanée : tranches totales vs déjà en cache."""
    job = config.coerce_job(job)
    if DEMO:
        return _demo().estimate(job)
    out = {"duration_s": None, "chunks": None, "derush_cached": 0, "speech_cached": 0}
    try:
        if job["mode"] == "generate":
            video = pipe.resolve_video(job)
        else:
            pdir = config.find_projects_dir(job.get("projects_dir") or None)
            if not pdir or not job.get("project"):
                return out
            res = project_info(job["project"], pdir)
            video = (job.get("video") or "").strip() or (res.get("info") or {}).get("video_path", "")
        if not video or not os.path.exists(video) or not media.have("ffprobe"):
            return out
        info = media.probe(video)
        out["duration_s"] = round(info["duration"], 1)
        out["chunks"] = max(1, int(info["duration"] // config.CHUNK_S) + 1)
        cdir = media.cache_dir_for(video)
        for fname, key_ in (("derush_chunks.json", "derush_cached"),
                            ("tracks_chunks.json", "speech_cached"),
                            ("analysis_chunks.json", "speech_cached")):
            p = os.path.join(cdir, fname)
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        out[key_] = max(out[key_], len((json.load(f).get("chunks") or {})))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def browse(path):
    home = os.path.expanduser("~")
    roots = [{"label": "Dossier du logiciel", "path": config.BASE_DIR},
             {"label": "Dossier personnel", "path": home}]
    for label, sub in (("Bureau", "Desktop"), ("Documents", "Documents"),
                       ("Vidéos", "Videos"), ("Téléchargements", "Downloads")):
        p = os.path.join(home, sub)
        if os.path.isdir(p):
            roots.append({"label": label, "path": p})
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            d = f"{letter}:\\"
            if os.path.isdir(d):
                roots.append({"label": f"Disque {letter}:", "path": d})
    path = os.path.abspath(path) if path else config.BASE_DIR
    if not os.path.isdir(path):
        path = os.path.dirname(path) or home
    entries = []
    try:
        names = sorted(os.listdir(path), key=lambda s: s.lower())
    except OSError:
        names = []
    for name in names:
        full = os.path.join(path, name)
        try:
            if os.path.isdir(full):
                entries.append({"name": name, "path": full, "dir": True, "size": None, "video": False})
            elif os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                entries.append({"name": name, "path": full, "dir": False,
                                "size": os.path.getsize(full), "video": True})
        except OSError:
            continue
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    parent = os.path.dirname(path.rstrip("\\/")) if os.path.dirname(path.rstrip("\\/")) != path else None
    return {"path": path, "parent": parent, "entries": entries[:400], "roots": roots}


# ------------------------------------------------------------------ lancement de job
def start_job(job, api_key=""):
    job = config.coerce_job(job)
    with _LOCK:
        if _STATE["thread"] and _STATE["thread"].is_alive():
            return {"ok": False, "error": "Un montage est déjà en cours."}
        if api_key and not DEMO:
            api_key = api_key.strip()
            if api_key and api_key != config.read_env_key("GEMINI_API_KEY"):
                try:
                    config.write_env_key("GEMINI_API_KEY", api_key)
                except OSError:
                    pass
                os.environ["GEMINI_API_KEY"] = api_key
        if not DEMO:
            config.save_settings(job)
        rep = Reporter([(s[0], s[1], s[2]) for s in pipe.stages_for(job)])
        cancel = CancelToken()
        _STATE.update(reporter=rep, cancel=cancel, job=job)

        def work():
            try:
                if DEMO:
                    _demo().simulate(job, rep, cancel)
                else:
                    pipe.run_job(job, rep, cancel)
            except Exception:
                if not rep.finished:   # filet : run_job/simulate terminent toujours, en principe
                    rep.finish("error", error="Erreur inattendue (voir autoshibi.log).")

        th = threading.Thread(target=work, daemon=True)
        _STATE["thread"] = th
        th.start()
    return {"ok": True}


def job_state():
    with _LOCK:
        rep, th = _STATE["reporter"], _STATE["thread"]
    if rep is None:
        return {"running": False, "snapshot": None, "history": []}
    return {"running": bool(th and th.is_alive()), "snapshot": rep.snapshot(),
            "history": list(rep.history)[-300:]}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):      # silence : pas de spam console
        pass

    # ----- utilitaires
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    # ----- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._static("index.html", "text/html; charset=utf-8")
            if u.path == "/app.css":
                return self._static("app.css", "text/css; charset=utf-8")
            if u.path == "/app.js":
                return self._static("app.js", "text/javascript; charset=utf-8")
            if u.path == "/api/bootstrap":
                return self._send(200, bootstrap())
            if u.path == "/api/state":
                return self._send(200, job_state())
            if u.path == "/api/events":
                return self._events()
            if u.path == "/api/projects":
                pdir = config.find_projects_dir((q.get("dir") or [""])[0] or None)
                if DEMO:
                    return self._send(200, {"projects_dir": bootstrap()["projects_dir"],
                                            "projects": bootstrap()["projects"]})
                return self._send(200, {"projects_dir": pdir or "",
                                        "projects": _projects_with_mtime(pdir)})
            if u.path == "/api/project-info":
                pdir = config.find_projects_dir((q.get("dir") or [""])[0] or None) or ""
                return self._send(200, project_info((q.get("name") or [""])[0], pdir))
            if u.path == "/api/browse":
                return self._send(200, browse((q.get("path") or [""])[0]))
            if u.path == "/api/assets":
                return self._send(200, self._assets())
            if u.path == "/api/asset":
                return self._asset((q.get("who") or ["AgentOMG"])[0], (q.get("n") or ["1"])[0])
            return self._send(404, {"error": "Page introuvable"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send(500, {"error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass

    def _static(self, name, ctype):
        path = os.path.join(WEB_DIR, name)
        if not os.path.exists(path):
            return self._send(404, {"error": f"Fichier manquant : {name}"})
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _assets(self):
        if DEMO:
            return {spk: list(range(1, 23)) for spk in config.SPEAKERS}
        out = {}
        for spk in config.SPEAKERS:
            folder = config.ASSET_DIRS[spk]
            out[spk] = [i for i in range(1, 23) if os.path.exists(os.path.join(folder, f"{i}.png"))]
        return out

    def _asset(self, who, n):
        who = who if who in config.SPEAKERS else "AgentOMG"
        try:
            n = max(1, min(22, int(n)))
        except ValueError:
            n = 1
        if DEMO:
            return self._send(200, _demo().fake_asset_svg(who, n), "image/svg+xml")
        path = os.path.join(config.ASSET_DIRS[who], f"{n}.png")
        if not os.path.exists(path):
            return self._send(404, {"error": "Image absente"})
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = queue.Queue()

        def push(ev):
            try:
                q.put(ev)
            except Exception:
                pass

        with _LOCK:
            rep = _STATE["reporter"]
        if rep is not None:
            for ev in list(rep.history):
                q.put(ev)
            rep.subscribe(push)
        try:
            while True:
                try:
                    ev = q.get(timeout=15)
                    payload = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    payload = ": ping\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if rep is not None:
                rep.unsubscribe(push)

    # ----- POST
    def do_POST(self):
        u = urlparse(self.path)
        body = self._json_body()
        try:
            if u.path == "/api/settings":
                job = config.coerce_job(body.get("job") or {})
                key = (body.get("api_key") or "").strip()
                if key and not DEMO:
                    config.write_env_key("GEMINI_API_KEY", key)
                    os.environ["GEMINI_API_KEY"] = key
                if not DEMO:
                    config.save_settings(job)
                return self._send(200, {"ok": True, "api_key_present": bool(config.gemini_api_key())})
            if u.path == "/api/preflight":
                return self._send(200, {"items": preflight(body.get("job") or {})})
            if u.path == "/api/estimate":
                return self._send(200, estimate(body.get("job") or {}))
            if u.path == "/api/video-info":
                return self._send(200, self._video_info((body.get("video") or "").strip()))
            if u.path == "/api/tracks":
                return self._send(200, self._tracks((body.get("video") or "").strip()))
            if u.path == "/api/run":
                job = body.get("job") or {}
                job["action"] = "run"
                return self._send(200, start_job(job, body.get("api_key") or ""))
            if u.path == "/api/check":
                job = body.get("job") or {}
                job["action"] = "check"
                return self._send(200, start_job(job, body.get("api_key") or ""))
            if u.path == "/api/cancel":
                with _LOCK:
                    cancel = _STATE["cancel"]
                if cancel:
                    cancel.cancel()
                return self._send(200, {"ok": True})
            if u.path == "/api/pick":
                return self._send(200, self._pick(body.get("kind") or "video"))
            if u.path == "/api/open-folder":
                return self._send(200, {"ok": self._open_folder((body.get("path") or "").strip())})
            if u.path == "/api/open-capcut":
                return self._send(200, {"ok": (not DEMO) and pipe.launch_capcut()})
            if u.path == "/api/clear-cache":
                return self._send(200, self._clear_cache((body.get("video") or "").strip()))
            return self._send(404, {"error": "Route inconnue"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass

    def _video_info(self, video):
        if DEMO:
            return {"ok": True, "info": _demo().VIDEO_INFO}
        if not video or not os.path.exists(video):
            return {"ok": False, "error": "Vidéo introuvable"}
        try:
            info = media.probe(video)
            info["cache"] = estimate({"mode": "generate", "video": video})
            return {"ok": True, "info": info}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _tracks(self, video):
        if DEMO:
            time.sleep(1.2)
            return {"ok": True, "streams": _demo().VIDEO_INFO["audio_streams"], "levels": _demo().TRACK_LEVELS}
        if not video or not os.path.exists(video):
            return {"ok": False, "error": "Vidéo introuvable : choisis-la d'abord"}
        try:
            info = media.probe(video)
            levels = media.track_levels(video, len(info["audio_streams"]))
            return {"ok": True, "streams": info["audio_streams"], "levels": levels}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _pick(self, kind):
        if DEMO:
            return {"ok": False, "error": "demo"}
        try:
            import sys
            r = subprocess.run([sys.executable, "-m", "autoshibi.picker", kind],
                               capture_output=True, text=True, timeout=300, cwd=config.BASE_DIR)
            path = (r.stdout or "").strip()
            return {"ok": bool(path), "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _open_folder(self, path):
        if DEMO:
            return False
        try:
            if not path:
                path = config.BASE_DIR
            if os.path.isfile(path):
                if os.name == "nt":
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                    return True
                path = os.path.dirname(path)
            if os.name == "nt":
                os.startfile(path)                       # noqa: S606 (local, chemin contrôlé par l'utilisateur)
            elif shutil.which("open"):
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return True
        except Exception:
            return False

    def _clear_cache(self, video):
        if DEMO:
            return {"ok": True}
        if not video or not os.path.exists(video):
            return {"ok": False, "error": "Choisis d'abord la vidéo"}
        try:
            pipe.clear_video_cache(media.cache_dir_for(video))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def serve(host, port):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
