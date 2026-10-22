"""
media.py — Tout ce qui touche à ffmpeg / ffprobe, optimisé :

- UN seul appel ffprobe pour tout savoir sur la vidéo (la v1 en lançait des
  dizaines, un par tranche) ;
- extraction audio en UN passage, avec les durées/offsets des tranches lus
  dans la liste du muxer segment (aucun ffprobe par fichier) ;
- les tranches audio extraites sont conservées dans le cache de la vidéo et
  réutilisées par le dérush ET la transcription (la v1 ré-extraisait tout) ;
- silences (silencedetect) mis en cache ;
- VAD en STREAMING : on ne charge plus des centaines de Mo d'audio en RAM,
  seuls les niveaux par tranche de 20 ms sont gardés (mêmes seuils que la v1).
"""
import os
import re
import sys
import json
import math
import array
import shutil
import hashlib
import subprocess
from operator import mul

from . import config

RATE = 16000
FRAME = 320                       # 20 ms
FRAME_S = FRAME / RATE
SILENCE_DB = -35
SILENCE_MIN = 0.15
MIN_CHUNK_S = 5                   # tranche plus courte : pas envoyée à Gemini
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def have(tool):
    return shutil.which(tool) is not None


# ------------------------------------------------------------------ exécution
def _run(cmd, cancel=None, capture=True):
    """Lance une commande ; si `cancel` est fourni, elle est tuée proprement à l'arrêt."""
    if cancel is None:
        return subprocess.run(cmd, capture_output=capture, text=True,
                              encoding="utf-8", errors="ignore", creationflags=_NO_WINDOW)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    cancel.register(proc)
    try:
        out, err = proc.communicate()
    finally:
        cancel.unregister(proc)
    cancel.check()
    txt = lambda b: (b or b"").decode("utf-8", "ignore")
    return subprocess.CompletedProcess(cmd, proc.returncode, txt(out), txt(err))


# ------------------------------------------------------------------ sondage
def probe(path):
    """Toutes les infos utiles de la vidéo en UN appel ffprobe."""
    r = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path])
    if r.returncode != 0:
        raise ValueError(f"ffprobe n'arrive pas à lire {path}")
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    vid = next((s for s in streams if s.get("codec_type") == "video"), {})
    dur = 0.0
    try:
        dur = float(data.get("format", {}).get("duration") or vid.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    audios = []
    for i, s in enumerate([s for s in streams if s.get("codec_type") == "audio"]):
        audios.append({
            "n": i + 1, "aidx": i, "channels": s.get("channels"),
            "codec": s.get("codec_name", ""), "title": (s.get("tags") or {}).get("title", ""),
        })
    return {
        "path": os.path.abspath(path),
        "width": int(vid.get("width") or 0), "height": int(vid.get("height") or 0),
        "duration": dur, "audio_streams": audios,
        "size": os.path.getsize(path) if os.path.exists(path) else 0,
    }


def probe_duration(path):
    return probe(path)["duration"]


def fingerprint(path):
    """Empreinte d'une vidéo pour son dossier de cache : chemin + taille + date
    + échantillons de contenu (début/milieu/fin) — rapide et sans collision
    quand un fichier est remplacé par un autre de même taille."""
    st = os.stat(path)
    h = hashlib.sha1()
    h.update(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode())
    try:
        with open(path, "rb") as f:
            for off in (0, max(0, st.st_size // 2 - 32768), max(0, st.st_size - 65536)):
                f.seek(off)
                h.update(f.read(65536))
    except OSError:
        pass
    return h.hexdigest()[:10]


def cache_dir_for(video_path):
    stem = re.sub(r"[^\w.-]+", "_", os.path.splitext(os.path.basename(video_path))[0])[:40]
    d = os.path.join(config.CACHE_ROOT, f"{stem}-{fingerprint(video_path)}")
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ tranches audio
class Chunk:
    __slots__ = ("index", "path", "offset", "duration")

    def __init__(self, index, path, offset, duration):
        self.index, self.path, self.offset, self.duration = index, path, offset, duration

    def to_json(self):
        return {"index": self.index, "file": os.path.basename(self.path),
                "offset": round(self.offset, 3), "duration": round(self.duration, 3)}


def extract_audio_chunks(video, aidx, cache_dir, chunk_s=config.CHUNK_S, cancel=None, rep=None):
    """
    Extrait la piste audio `aidx` (0 = piste 1) en mp3 mono léger par tranches.
    Résultat mis en cache dans cache_dir/audio/a{aidx}/ : un relancement ne
    ré-extrait rien, et dérush + transcription partagent les mêmes fichiers.
    """
    out_dir = os.path.join(cache_dir, "audio", f"a{aidx}")
    manifest = os.path.join(out_dir, "chunks.json")
    key = hashlib.sha1(f"{os.path.abspath(video)}|{aidx}|{chunk_s}|v2".encode()).hexdigest()[:12]
    if os.path.exists(manifest):
        try:
            with open(manifest, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("key") == key and all(os.path.exists(os.path.join(out_dir, c["file"]))
                                              for c in data["chunks"]):
                return [Chunk(c["index"], os.path.join(out_dir, c["file"]),
                              float(c["offset"]), float(c["duration"])) for c in data["chunks"]]
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "audio_%03d.mp3")
    list_csv = os.path.join(out_dir, "segments.csv")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", "-map", f"0:a:{aidx}",
           "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
           "-f", "segment", "-segment_time", str(chunk_s), "-reset_timestamps", "1",
           "-segment_list", list_csv, "-segment_list_type", "csv", pattern]
    r = _run(cmd, cancel=cancel)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg n'a pas pu extraire la piste audio {aidx + 1} : "
                           f"{(r.stderr or '')[-300:]}")

    files = sorted(f for f in os.listdir(out_dir) if f.startswith("audio_") and f.endswith(".mp3"))
    chunks = []
    # offsets/durées depuis la liste du muxer segment (filename,start,end)
    listed = {}
    try:
        with open(list_csv, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    listed[os.path.basename(parts[0])] = (float(parts[1]), float(parts[2]))
    except (OSError, ValueError):
        listed = {}
    off = 0.0
    for i, name in enumerate(files):
        if name in listed:
            a, b = listed[name]
            chunks.append(Chunk(i, os.path.join(out_dir, name), off, max(0.0, b - a)))
            off += max(0.0, b - a)
        else:  # secours (très rare) : un ffprobe par fichier
            d = probe_duration(os.path.join(out_dir, name))
            chunks.append(Chunk(i, os.path.join(out_dir, name), off, d))
            off += d
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"key": key, "chunks": [c.to_json() for c in chunks]}, f)
    return chunks


# ------------------------------------------------------------------ silences (dérush)
def detect_silences(video, total, cache_dir=None, cancel=None, rep=None):
    """Silences du mix global (silencedetect -35 dB), mis en cache par vidéo."""
    path = os.path.join(cache_dir, "silences.json") if cache_dir else None
    key = f"{SILENCE_DB}|{SILENCE_MIN}|v2"
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("key") == key:
                return [tuple(x) for x in data["silences"]]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    if rep:
        rep.log(f"Détection des silences ({SILENCE_DB} dB)…")
    r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vn", "-map", "0:a:0",
              "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
             cancel=cancel)
    silences, start = [], None
    for line in (r.stderr or "").splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and start is not None:
            silences.append((start, min(float(m.group(1)), total)))
            start = None
    if start is not None:
        silences.append((start, total))
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "silences": silences}, f)
    if rep:
        rep.log(f"{len(silences)} silences détectés sur le mix.")
    return silences


# ------------------------------------------------------------------ VAD (qui parle)
def vad_params(rel_db=None):
    """Seuils de détection de parole — mêmes valeurs et formules que la v1."""
    def envf(name, default):
        try:
            return float(os.getenv(name, default))
        except ValueError:
            return float(default)
    return {
        "rel_db": float(rel_db) if rel_db is not None else envf("VAD_REL_DB", 24),
        "noise_margin_db": envf("VAD_NOISE_MARGIN_DB", 8),
        "abs_min_db": envf("VAD_ABS_MIN_DB", -50),
        "fill_s": envf("VAD_FILL_S", 0.30),
        "min_s": envf("VAD_MIN_S", 0.20),
    }


def _frame_db_stream(video, aidx, cancel=None, limit_s=None):
    """Niveau (dBFS RMS) par tranche de 20 ms, lu en streaming depuis ffmpeg.
    Même calcul que la v1, sans charger tout le PCM en mémoire."""
    cmd = ["ffmpeg", "-v", "error", "-i", video]
    if limit_s:
        cmd += ["-t", str(limit_s)]
    cmd += ["-map", f"0:a:{aidx}", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    if cancel:
        cancel.register(proc)
    full = 32768.0 ** 2
    dbs = array.array("f")
    per_block_frames = 50                     # blocs d'1 seconde, alignés sur les frames
    want = per_block_frames * FRAME * 2
    try:
        while True:
            if cancel:
                cancel.check()
            raw = proc.stdout.read(want)
            if not raw:
                break
            if len(raw) % 2:
                raw = raw[:-1]
            arr = array.array("h")
            arr.frombytes(raw)
            if sys.byteorder == "big":
                arr.byteswap()
            n = len(arr) // FRAME
            for i in range(n):
                sl = arr[i * FRAME:(i + 1) * FRAME]
                dbs.append(10 * math.log10(sum(map(mul, sl, sl)) / FRAME / full + 1e-12))
            if len(raw) < want:
                break
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()
        if cancel:
            cancel.unregister(proc)
    return dbs


def detect_speech(video, aidx, prm=None, cancel=None):
    """Intervalles où la personne de cette piste parle : ([(début, fin)], infos)."""
    prm = prm or vad_params()
    db = _frame_db_stream(video, aidx, cancel=cancel)
    if not db:
        return [], {"note": "piste vide"}
    s = sorted(db)
    noise, peak = s[int(0.10 * (len(s) - 1))], s[int(0.95 * (len(s) - 1))]
    info = {"noise_db": round(noise, 1), "peak_db": round(peak, 1)}
    if peak < -60 or peak - noise < 10:
        info["note"] = "piste silencieuse ou sans parole nette"
        return [], info
    thr = max(noise + prm["noise_margin_db"], peak - prm["rel_db"])
    thr = max(thr, min(prm["abs_min_db"], peak - 12))
    info["threshold_db"] = round(thr, 1)

    runs, i, n = [], 0, len(db)
    while i < n:
        if db[i] > thr:
            j = i
            while j < n and db[j] > thr:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    fill, merged = int(round(prm["fill_s"] / FRAME_S)), []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= fill:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    minf = int(round(prm["min_s"] / FRAME_S))
    return [(max(0.0, a * FRAME_S - 0.04), b * FRAME_S + 0.06)
            for a, b in merged if b - a >= minf], info


def speech_intervals(video, tracks_map, cache_dir, rel_db=None, cancel=None, rep=None):
    """Parole par personnage {nom: [(début, fin), ...]}, mise en cache."""
    prm = vad_params(rel_db)
    key = hashlib.md5(json.dumps([prm, tracks_map], sort_keys=True).encode()).hexdigest()[:10]
    path = os.path.join(cache_dir, "vad.json") if cache_dir else None
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("key") == key:
                if rep:
                    rep.log("Détection de parole : reprise du cache (VAD déjà fait).")
                return {k: [tuple(x) for x in v] for k, v in data["speech"].items()}
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    speech, infos = {}, {}
    for spk, aidx in tracks_map.items():
        if rep:
            rep.log(f"Détection de la parole sur la piste de {spk} (piste {aidx + 1})…")
        speech[spk], infos[spk] = detect_speech(video, aidx, prm, cancel=cancel)
        if infos[spk].get("note") and rep:
            rep.warn(f"{spk} : {infos[spk]['note']} — vérifie le numéro de sa piste.")
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "speech": speech, "info": infos}, f)
    return speech


def track_levels(video, n_streams, limit_s=180, cancel=None):
    """Aperçu rapide de chaque piste (échantillon) pour aider à les attribuer :
    niveau moyen/crête + part « voix » estimée. Utilisé par 🔍 Détecter."""
    out = []
    prm = vad_params()
    for aidx in range(n_streams):
        db = _frame_db_stream(video, aidx, cancel=cancel, limit_s=limit_s)
        if not db:
            out.append({"n": aidx + 1, "aidx": aidx, "mean_db": None, "peak_db": None, "voice": 0.0})
            continue
        s = sorted(db)
        peak = s[int(0.95 * (len(s) - 1))]
        noise = s[int(0.10 * (len(s) - 1))]
        thr = max(noise + prm["noise_margin_db"], peak - prm["rel_db"])
        voice = sum(1 for x in db if x > thr) / len(db) if peak - noise >= 10 else 0.0
        mean = sum(db) / len(db)
        out.append({"n": aidx + 1, "aidx": aidx, "mean_db": round(mean, 1),
                    "peak_db": round(peak, 1), "voice": round(voice, 3),
                    "silent": bool(peak < -60 or peak - noise < 10)})
    return out


# ------------------------------------------------------------------ intervalles
def overlap_total(a, b):
    """Durée totale de l'intersection de deux listes d'intervalles triés."""
    t, i, j = 0.0, 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if e > s:
            t += e - s
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return t


def merge_ranges(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract_ranges(total, removes):
    keep, cur = [], 0.0
    for a, b in merge_ranges(removes):
        if a > cur:
            keep.append((cur, a))
        cur = max(cur, b)
    if cur < total:
        keep.append((cur, total))
    return keep
