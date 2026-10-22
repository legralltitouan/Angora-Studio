"""
config.py — Réglages du logiciel (v2).

Tout est centralisé ici : schéma des réglages d'un « job », constantes métier
(placement des shibis, modèles Gemini...), persistance JSON et lecture de .env.
Ce module n'a AUCUNE dépendance externe : l'interface et les vérifications
démarrent même si les bibliothèques Gemini ne sont pas installées.

Compatibilité : les anciens `gui_settings.json` (v1) sont repris
automatiquement au premier lancement de la v2.
"""
import os
import json
import copy

# ------------------------------------------------------------------ chemins
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(PKG_DIR)                       # dossier du logiciel
SETTINGS_PATH = os.path.join(BASE_DIR, "autoshibi_settings.json")
OLD_SETTINGS_PATH = os.path.join(BASE_DIR, "gui_settings.json")   # v1 (migration)
ENV_PATH = os.path.join(BASE_DIR, ".env")
CACHE_ROOT = os.path.join(BASE_DIR, "cache")
STYLE_PATH = os.path.join(BASE_DIR, "style_profile.json")
LOG_PATH = os.path.join(BASE_DIR, "autoshibi.log")

ASSETS_DIR = os.path.join(BASE_DIR, "Assets")
ASSETS_OMG = os.path.join(ASSETS_DIR, "AgentOMG Pose")
ASSETS_MARVOUCH = os.path.join(ASSETS_DIR, "Marvouch Pose")
SPEAKERS = ("AgentOMG", "Marvouch")
ASSET_DIRS = {"AgentOMG": ASSETS_OMG, "Marvouch": ASSETS_MARVOUCH}
N_POSES = 22

# ------------------------------------------------------------------ Gemini
# Cascade de modèles (inchangée par rapport à la v1 : même qualité d'analyse).
# Surcharge possible dans .env : GEMINI_MODELS=modele1,modele2,...
DEFAULT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
GEMINI_HOST = "generativelanguage.googleapis.com"
CHUNK_S = 300          # durée d'une tranche envoyée à Gemini (5 min, comme la v1)

# ------------------------------------------------------------------ shibis
# Placement calé sur les réglages CapCut de l'utilisateur (valeurs de la v1,
# vérifiées sur de vrais montages — ne pas modifier sans tester dans CapCut).
SHIBI_PLACEMENT = {
    "face":  {"scale": 1.0, "edge_x": 0.0318, "overshoot_y": 0.0991},   # poses 1-12
    "biais": {"scale": 1.0, "edge_x": 0.0787, "overshoot_y": 0.0722},   # poses 13-22
}
Y_AXIS_UP = True       # si les shibis apparaissent en HAUT au lieu du bas : False
PHOTO_DURATION_US = 10_800_000_000    # durée d'un matériau « photo » CapCut (3 h)

# Vidéos YouTube de référence pour le profil de style (inchangées)
STYLE_URLS = [
    "https://youtu.be/wtrlj0rFfcE",
    "https://youtu.be/KSWZu6p3XMY",
]

# ------------------------------------------------------------------ schéma d'un job
# Un « job » = tout ce que l'interface envoie au moteur. Mêmes clés que la v1
# (+ quelques ajouts v2) : les anciens réglages sont donc repris tels quels.
DEFAULT_JOB = {
    "action": "run",                # run | check
    "mode": "edit",                 # generate = montage depuis la vidéo | edit = projet déjà monté

    # projets CapCut
    "projects_dir": "",             # vide = détecté automatiquement
    "template": "AutoShibi-V4",     # projet modèle
    "project": "",                  # projet à modifier (mode edit)
    "work_on_copy": False,
    "close_capcut": True,
    "video": "",                    # generate : vidéo à monter | edit : vide = celle du projet

    # dérush (generate uniquement)
    "derush": True,
    "derush_level": 3,              # 1 léger / 2 normal / 3 serré
    "max_pause_s": 0.6,
    "max_shot_s": 7.0,

    # zooms
    "zoom": True,
    "zoom_coverage": 0.80,
    "zoom_intensity": 1.0,
    "zoom_out_ratio": 0.45,
    "keep_existing_zoom": True,

    # shibis
    "shibi": True,
    "shibi_source": "tracks",       # tracks (exact) | ai (deviné) | off
    "track_agentomg": 2,
    "track_marvouch": 3,
    "vad_rel_db": 24,
    "shibi_scale": 1.0,
    "swap_sides": False,
    "linger_s": 0.0,
    "max_hold": 3.2,
    "replace_old_shibis": True,
    "repick": False,

    # avancé
    "fresh": False,
    "workers": 3,                   # requêtes Gemini en parallèle

    # v2
    "preset": "equilibre",          # equilibre | rythme | doux | zooms | shibis | perso
    "open_capcut": False,           # rouvrir CapCut à la fin
}

# Recettes prédéfinies : un clic dans l'interface applique un faisceau cohérent.
PRESETS = {
    "equilibre": {"derush": True, "derush_level": 3, "max_pause_s": 0.6, "max_shot_s": 7.0,
                  "zoom": True, "zoom_coverage": 0.80, "zoom_intensity": 1.0, "zoom_out_ratio": 0.45,
                  "shibi": True},
    "rythme":    {"derush": True, "derush_level": 3, "max_pause_s": 0.4, "max_shot_s": 5.0,
                  "zoom": True, "zoom_coverage": 0.95, "zoom_intensity": 1.3, "zoom_out_ratio": 0.50,
                  "shibi": True, "max_hold": 2.6},
    "doux":      {"derush": True, "derush_level": 1, "max_pause_s": 1.0, "max_shot_s": 10.0,
                  "zoom": True, "zoom_coverage": 0.55, "zoom_intensity": 0.6, "zoom_out_ratio": 0.40,
                  "shibi": True, "linger_s": 0.3},
    "zooms":     {"zoom": True, "shibi": False},
    "shibis":    {"zoom": False, "shibi": True},
}


def default_job():
    return copy.deepcopy(DEFAULT_JOB)


def coerce_job(raw):
    """Nettoie un job reçu (interface, CLI, ancien fichier) : clés connues + bons types."""
    job = default_job()
    if not isinstance(raw, dict):
        return job
    for k, v in raw.items():
        if k not in DEFAULT_JOB:
            continue
        kind = type(DEFAULT_JOB[k])
        try:
            job[k] = kind(v) if kind in (bool, int, float, str) else v
        except (TypeError, ValueError):
            job[k] = DEFAULT_JOB[k]
    job["derush_level"] = max(1, min(3, int(job["derush_level"])))
    job["workers"] = max(1, min(6, int(job["workers"])))
    job["track_agentomg"] = max(1, int(job["track_agentomg"]))
    job["track_marvouch"] = max(1, int(job["track_marvouch"]))
    if job["mode"] not in ("edit", "generate"):
        job["mode"] = "edit"
    if job["shibi_source"] not in ("tracks", "ai", "off"):
        job["shibi_source"] = "tracks"
    return job


def load_settings():
    """Réglages enregistrés (autoshibi_settings.json, sinon reprise de la v1)."""
    for path in (SETTINGS_PATH, OLD_SETTINGS_PATH):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return coerce_job(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return default_job()


def save_settings(job):
    try:
        data = {k: v for k, v in coerce_job(job).items() if k != "action"}
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_PATH)
    except OSError:
        pass


# ------------------------------------------------------------------ .env
def read_env_key(key, path=ENV_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def write_env_key(key, value, path=ENV_PATH):
    """Met à jour (ou ajoute) KEY=value dans .env sans toucher aux autres lignes."""
    lines = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        pass
    done = False
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            done = True
    if not done:
        lines.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def gemini_api_key():
    return (os.getenv("GEMINI_API_KEY") or read_env_key("GEMINI_API_KEY") or "").strip()


def gemini_models():
    raw = (os.getenv("GEMINI_MODELS") or read_env_key("GEMINI_MODELS") or "").strip()
    if raw:
        models = [m.strip() for m in raw.replace(";", ",").split(",") if m.strip()]
        if models:
            return models
    return list(DEFAULT_MODELS)


def find_projects_dir(explicit=None):
    """Dossier des projets CapCut (Windows ou macOS), ou celui indiqué / de .env."""
    home = os.path.expanduser("~")
    cands = [explicit or os.getenv("CAPCUT_PROJECTS_DIR") or read_env_key("CAPCUT_PROJECTS_DIR")]
    if os.environ.get("LOCALAPPDATA"):
        cands.append(os.path.join(os.environ["LOCALAPPDATA"], "CapCut", "User Data",
                                  "Projects", "com.lveditor.draft"))
    cands += [
        os.path.join(home, "AppData", "Local", "CapCut", "User Data", "Projects", "com.lveditor.draft"),
        os.path.join(home, "Movies", "CapCut", "User Data", "Projects", "com.lveditor.draft"),
    ]
    return next((c for c in cands if c and os.path.isdir(c)), None)
