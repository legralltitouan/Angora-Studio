"""
checks.py — Vérifications avant montage, en données structurées.

L'interface les affiche en direct (✓ / ⚠ / ✗ avec l'action à faire), la CLI
les imprime, le moteur s'en sert pour refuser de démarrer dans le vide.
"""
import os
import ssl
import socket
import tempfile

from . import config, media, capcut, derush, projedit

Item = dict   # {"status": "ok"|"warn"|"fail", "label": str, "hint": str}


def _item(status, label, hint=""):
    return {"status": status, "label": label, "hint": hint}


# ------------------------------------------------------------------ réseau Gemini
def _peer_cert_info(address):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(address, timeout=8) as sock:
        with ctx.wrap_socket(sock, server_hostname=config.GEMINI_HOST) as s:
            der = s.getpeercert(True)
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(ssl.DER_cert_to_PEM_cert(der))
        name = f.name
    try:
        info = ssl._ssl._test_decode_cert(name)
    finally:
        os.unlink(name)
    flat = lambda t: ", ".join(f"{k}={v}" for rdn in t for (k, v) in rdn)
    return flat(info.get("subject", ())), flat(info.get("issuer", ()))


def check_gemini_network():
    """Teste la connexion TLS vers Gemini comme le fait la bibliothèque."""
    host = config.GEMINI_HOST
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        return [_item("fail", f"Impossible de joindre {host} ({e})",
                      "Pas d'internet, ou DNS bloqué ?")]
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                pass
        return [_item("ok", f"Connexion sécurisée à Gemini OK ({ip})")]
    except ssl.SSLCertVerificationError as e:
        try:
            subj, issuer = _peer_cert_info((host, 443))
            seen = f" Certificat reçu : sujet [{subj}], émis par [{issuer}]."
        except Exception:
            seen = ""
        return [_item("fail", f"Certificat invalide reçu de {host} ({ip}) : {e.verify_message}.{seen}",
                      "Antivirus avec analyse HTTPS, VPN/proxy, filtre DNS, contrôle parental ou "
                      "fichier hosts : teste en partage de connexion du téléphone.")]
    except (OSError, socket.timeout) as e:
        return [_item("fail", f"Connexion à {host} impossible : {e}")]


# ------------------------------------------------------------------ briques communes
def check_tools():
    out = []
    for tool in ("ffmpeg", "ffprobe"):
        out.append(_item("ok", f"{tool} installé") if media.have(tool) else
                   _item("fail", f"{tool} introuvable", "Installe-le : winget install ffmpeg (puis rouvre le logiciel)"))
    return out


def check_key():
    return [_item("ok", "Clé Gemini trouvée") if config.gemini_api_key() else
            _item("fail", "Clé Gemini manquante", "Renseigne-la dans « Réglages avancés » "
                  "(gratuite sur aistudio.google.com/apikey) ou dans .env")]


def check_assets():
    out = []
    for spk in config.SPEAKERS:
        folder = config.ASSET_DIRS[spk]
        missing = [i for i in range(1, config.N_POSES + 1)
                   if not os.path.exists(os.path.join(folder, f"{i}.png"))]
        out.append(_item("ok", f"Poses {spk} : 22 images") if not missing else
                   _item("fail", f"Poses {spk} : images manquantes {missing}", f"Dossier attendu : {folder}"))
    return out


def check_video(video, job):
    """(items, info|None) — info = résultat media.probe si la vidéo est lisible."""
    if not video or not os.path.exists(video):
        return [_item("fail", f"Vidéo introuvable : {video or '(aucune)'}",
                      "Choisis le fichier du rec (.mkv, .mp4…)")], None
    if not media.have("ffprobe"):
        return [], None
    try:
        info = media.probe(video)
    except Exception:
        return [_item("fail", f"ffprobe n'arrive pas à lire {os.path.basename(video)}")], None
    out = [_item("ok", f"Vidéo : {os.path.basename(video)} "
                       f"({info['width']}x{info['height']}, {info['duration'] / 60:.1f} min, "
                       f"{len(info['audio_streams'])} piste(s) audio)")]
    if job.get("shibi") and job.get("shibi_source") == "tracks":
        from . import speech
        mapping, msg = speech.resolve(info, job["track_agentomg"], job["track_marvouch"])
        out.append(_item("ok", f"Pistes audio séparées : {msg} → qui parle est exact") if mapping else
                   _item("warn", f"Pistes audio séparées indisponibles ({msg})",
                         "Gemini devra deviner qui parle (moins fiable)"))
    elif job.get("shibi"):
        out.append(_item("warn", "Qui parle : deviné par l'IA", "Moins fiable que les pistes séparées"))
    return out, info


def check_projects_dir(job):
    d = config.find_projects_dir(job.get("projects_dir") or None)
    if not d:
        return [_item("fail", "Dossier des projets CapCut introuvable",
                      "Indique-le dans l'interface, ou CAPCUT_PROJECTS_DIR dans .env")], None
    return [_item("ok", f"Projets CapCut : {d}")], d


# ------------------------------------------------------------------ mode génération
def check_generate(job, video, full=False):
    out = []
    out += check_tools()
    need_gemini = job["derush"] or (job["shibi"] and job["shibi_source"] != "off")
    if need_gemini:
        out += check_key()
        if full:
            out += check_gemini_network()
    items, _info = check_video(video, job)
    out += items
    if job["shibi"] and job["shibi_source"] != "off":
        out += check_assets()
    pitems, pdir = check_projects_dir(job)
    out += pitems
    if pdir:
        template = job.get("template") or "AutoShibi-V4"
        tdir = os.path.join(pdir, template)
        if not os.path.isdir(tdir):
            avail = capcut.list_projects(pdir)
            out.append(_item("fail", f"Projet modèle « {template} » introuvable",
                             f"Projets existants : {', '.join(avail[:8]) or 'aucun'} — crée-le (voir le guide) "
                             "ou choisis-en un autre"))
        else:
            out.append(_item("ok", f"Projet modèle : {template}"))
            out += [_item(s, m) for s, m in capcut.inspect_template(tdir)]
    return out


# ------------------------------------------------------------------ mode édition
def sample_from_template(job, projects_dir):
    name = job.get("template")
    if not name:
        return None
    tdir = os.path.join(projects_dir, name)
    for path in capcut.draft_files(tdir) if os.path.isdir(tdir) else []:
        try:
            sample = derush.learn_scale_keyframes(capcut.load_draft(path))
        except ValueError:
            continue
        if sample:
            return sample
    return None


def check_edit(job, full=False):
    out = []
    want_zoom = bool(job["zoom"])
    want_shibi = bool(job["shibi"]) and job["shibi_source"] != "off"
    pitems, pdir = check_projects_dir(job)
    out += pitems
    if not pdir:
        return out
    name = (job.get("project") or "").strip()
    if not name:
        out.append(_item("fail", "Aucun projet choisi", "Sélectionne le projet CapCut à modifier"))
        return out
    pdir_proj = os.path.join(pdir, name)
    files = capcut.draft_files(pdir_proj)
    if not files:
        out.append(_item("fail", f"« {name} » : aucun draft_content.json",
                         "Ouvre le projet une fois dans CapCut, puis ferme CapCut"))
        return out
    video = None
    try:
        draft = capcut.load_draft(files[0])
        info = projedit.summarize(draft)
    except (ValueError, RuntimeError) as e:
        out.append(_item("fail", str(e)))
        return out
    out.append(_item("ok", f"Projet « {name} » : {info['clips']} plans, {info['duration_s'] / 60:.1f} min, "
                           f"canvas {info['canvas'][0]}x{info['canvas'][1]}"))
    if info["other_videos"]:
        out.append(_item("warn", f"{info['other_videos']} autre(s) fichier(s) vidéo sur la piste principale",
                         "Seuls les plans du fichier principal recevront des shibis"))
    if info["speed_changed"]:
        out.append(_item("warn", f"{info['speed_changed']} plan(s) à vitesse modifiée : ignorés pour les zooms"))
    if info["with_scale_keyframes"]:
        out.append(_item("ok", f"{info['with_scale_keyframes']} plan(s) déjà zoomés "
                               + ("(conservés)" if job["keep_existing_zoom"] else "(seront refaits)")))
    if info["previous_shibi_tracks"]:
        out.append(_item("ok", f"{info['previous_shibi_tracks']} piste(s) de shibis déjà posée(s) "
                               + ("(seront remplacées)" if job["replace_old_shibis"] else "(conservées)")))
    if want_zoom:
        if info["has_keyframe_sample"] or sample_from_template(job, pdir):
            out.append(_item("ok", "Exemple de keyframe d'échelle trouvé : format de TA version de CapCut"))
        else:
            out.append(_item("warn", "Aucun exemple de keyframe d'échelle (ni projet, ni modèle)",
                             "Les zooms risquent de ne pas s'animer : ajoute un keyframe Échelle à la main "
                             "sur un plan, ou choisis un modèle qui en contient"))
    video = (job.get("video") or "").strip() or info["video_path"]
    if want_shibi:
        out += check_tools()
        out += check_key()
        if full:
            out += check_gemini_network()
        out += check_assets()
        items, _ = check_video(video, job)
        out += items
    return out


def has_fail(items):
    return any(i["status"] == "fail" for i in items)


def render(items, rep, stage=None):
    icons = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    for i in items:
        level = "ok" if i["status"] == "ok" else ("warn" if i["status"] == "warn" else "err")
        rep.log(f"{icons[i['status']]} {i['label']}" + (f" — {i['hint']}" if i["hint"] else ""),
                level=level, stage=stage)
