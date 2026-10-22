"""
capcut.py — Lecture/écriture des projets CapCut (draft_content.json).

Toutes les recettes délicates de la v1 sont conservées telles quelles
(clonage avec renouvellement des UUID, registre root_meta_info.json,
matériaux photo, placement des shibis au demi-canvas près, temps des
keyframes en temps source...). Structure nettoyée : plus aucun global
mutable, le style des shibis est un objet passé explicitement.
"""
import os
import re
import copy
import json
import time
import uuid
import struct
import shutil

from . import config, derush, poses

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


# ------------------------------------------------------------------ style des shibis
class ShibiStyle:
    """Taille, côtés, miroirs et maintien des shibis pour un job donné."""

    def __init__(self, scale=1.0, swap_sides=False, linger_s=0.0):
        self.placement = copy.deepcopy(config.SHIBI_PLACEMENT)
        for k in self.placement:
            self.placement[k]["scale"] = float(scale)
        self.side = {"Marvouch": "droite" if swap_sides else "gauche",
                     "AgentOMG": "gauche" if swap_sides else "droite"}
        # le shibi regarde toujours vers le centre de l'écran
        self.flip = {"Marvouch": not swap_sides, "AgentOMG": bool(swap_sides)}
        self.linger_s = float(linger_s)


# ------------------------------------------------------------------ fichiers d'un projet
def draft_files(project_dir):
    files = []
    root = os.path.join(project_dir, "draft_content.json")
    if os.path.exists(root):
        files.append(root)
    tl_root = os.path.join(project_dir, "Timelines")
    if os.path.isdir(tl_root):
        for d in sorted(os.listdir(tl_root)):
            p = os.path.join(tl_root, d, "draft_content.json")
            if os.path.exists(p):
                files.append(p)
    return files


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_draft(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            draft = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"draft_content.json illisible ({e}). "
                         "Ouvre le projet une fois dans CapCut puis ferme CapCut.")
    if not isinstance(draft, dict) or "tracks" not in draft:
        raise ValueError(f"{path} : ce n'est pas un projet CapCut lisible (pas de pistes).")
    return draft


def save_draft(path, draft):
    """Écriture atomique + synchronisation du .bak comme le fait CapCut."""
    tmp = path + ".autoshibi.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(draft, f, separators=(",", ":"))
    os.replace(tmp, path)
    try:
        shutil.copy2(path, path + ".bak")
    except OSError:
        pass


def list_projects(projects_dir):
    """Noms des projets, du plus récemment modifié au plus ancien."""
    if not projects_dir or not os.path.isdir(projects_dir):
        return []
    out = []
    for d in os.listdir(projects_dir):
        full = os.path.join(projects_dir, d)
        if os.path.isdir(full):
            files = draft_files(full)
            if files:
                try:
                    mt = max(os.path.getmtime(p) for p in files)
                except OSError:
                    mt = 0
                out.append((mt, d))
    return [d for _, d in sorted(out, reverse=True)]


# ------------------------------------------------------------------ clonage du projet modèle
def _style_like(path, like):
    if "/" in like and "\\" not in like:
        return path.replace("\\", "/")
    if "\\" in like and "/" not in like:
        return path.replace("/", "\\")
    return path


def _is_uuid(s):
    return isinstance(s, str) and UUID_RE.fullmatch(s) is not None


def detect_template_ids(project_dir):
    """Identifiants propres au projet à renouveler pour que le clone soit neuf."""
    tl_root = os.path.join(project_dir, "Timelines")
    tl_ids = []
    if os.path.isdir(tl_root):
        tl_ids = [d for d in sorted(os.listdir(tl_root))
                  if os.path.isdir(os.path.join(tl_root, d)) and _is_uuid(d)]
    other = set()
    meta = load_json(os.path.join(project_dir, "draft_meta_info.json"))
    if isinstance(meta, dict):
        for k, v in meta.items():
            if "id" in k.lower() and _is_uuid(v):
                other.add(v)
    proj = load_json(os.path.join(tl_root, "project.json"))
    if proj is not None:
        other.update(UUID_RE.findall(json.dumps(proj)))
    for p in draft_files(project_dir):
        d = load_json(p)
        if isinstance(d, dict) and _is_uuid(d.get("id")):
            other.add(d["id"])
    tl_low = {t.lower() for t in tl_ids}
    return tl_ids, sorted(o for o in other if o.lower() not in tl_low)


def clone_project(src_dir, dst_dir, new_name, rep=None):
    """Copie un projet en renouvelant tous ses identifiants.
    Retourne (project_id, timeline_id) du clone."""
    shutil.copytree(src_dir, dst_dir)
    tl_ids, other = detect_template_ids(dst_dir)
    mapping, new_tid = {}, None
    tl_root = os.path.join(dst_dir, "Timelines")
    for t in tl_ids:
        nid = str(uuid.uuid4()).upper()
        mapping[t.lower()] = nid
        os.rename(os.path.join(tl_root, t), os.path.join(tl_root, nid))
        new_tid = new_tid or nid
    for o in other:
        mapping[o.lower()] = str(uuid.uuid4()).upper()
    if rep:
        rep.log(f"Clonage : {len(tl_ids)} id de timeline, {len(other)} id de projet/draft renouvelés.")
        if not mapping:
            rep.warn("Aucun ID détecté dans le modèle : le clone risque de se confondre avec l'original.")
    if mapping:
        pattern = re.compile("|".join(re.escape(k) for k in mapping), re.IGNORECASE)
        replaced = 0
        for root, _dirs, files in os.walk(dst_dir):
            for file in files:
                if file.endswith((".json", ".bak", ".tmp")) or "meta_info" in file:
                    path = os.path.join(root, file)
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            content = f.read()
                        content, n = pattern.subn(lambda m: mapping[m.group(0).lower()], content)
                        if n:
                            with open(path, "w", encoding="utf-8") as f:
                                f.write(content)
                            replaced += n
                    except Exception:
                        pass
        if rep:
            rep.log(f"{replaced} occurrences d'ID remplacées dans le clone.")

    new_pid = None
    meta_path = os.path.join(dst_dir, "draft_meta_info.json")
    meta = load_json(meta_path)
    if isinstance(meta, dict):
        if "draft_name" in meta:
            meta["draft_name"] = new_name
        if "draft_fold_path" in meta:
            meta["draft_fold_path"] = _style_like(dst_dir, meta["draft_fold_path"])
        new_pid = meta.get("draft_id") if _is_uuid(meta.get("draft_id")) else None
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, separators=(",", ":"))
        except OSError as e:
            if rep:
                rep.warn(f"draft_meta_info.json non mis à jour : {e}")
    return new_pid or str(uuid.uuid4()).upper(), new_tid or str(uuid.uuid4()).upper()


def register_in_registry(projects_dir, ref_dir, new_dir, new_name, new_pid, new_tid, rep=None):
    """Inscrit le nouveau projet dans root_meta_info.json (avec sauvegarde)."""
    root_meta = os.path.join(projects_dir, "root_meta_info.json")
    shutil.copy2(root_meta, root_meta + f".backup_{int(time.time())}")
    with open(root_meta, "r", encoding="utf-8") as f:
        root_info = json.load(f)
    store = root_info.setdefault("all_draft_store", [])
    ref_norm = os.path.normcase(os.path.normpath(ref_dir))
    tmpl = next((e for e in store if e.get("draft_fold_path") and
                 os.path.normcase(os.path.normpath(e["draft_fold_path"])) == ref_norm), None)
    now = int(time.time() * 1_000_000)
    new_json = os.path.join(new_dir, "draft_content.json")
    if tmpl:
        entry = copy.deepcopy(tmpl)
        like = tmpl["draft_fold_path"]
        values = {"draft_id": new_pid, "id": new_pid, "draft_name": new_name, "name": new_name,
                  "draft_fold_path": _style_like(new_dir, like),
                  "draft_json_file": _style_like(new_json, tmpl.get("draft_json_file", like)),
                  "tm_draft_create": now, "tm_draft_modified": now}
        for k, v in values.items():
            if k in entry:
                entry[k] = v
        jf = tmpl.get("draft_json_file", "")
        if "Timelines" in jf:
            entry["draft_json_file"] = _style_like(
                os.path.join(new_dir, "Timelines", new_tid, "draft_content.json"), jf)
    else:
        entry = {"id": new_pid, "draft_id": new_pid, "name": new_name, "draft_name": new_name,
                 "draft_fold_path": new_dir, "draft_json_file": new_json,
                 "draft_root_path": projects_dir, "tm_draft_create": now, "tm_draft_modified": now}
    store.insert(0, entry)
    with open(root_meta, "w", encoding="utf-8") as f:
        json.dump(root_info, f, separators=(",", ":"))


# ------------------------------------------------------------------ inspection du modèle
def canvas_size(draft):
    c = draft.get("canvas_config") or {}
    try:
        return float(c["width"]), float(c["height"])
    except (KeyError, TypeError, ValueError):
        return 1920.0, 1080.0


def inspect_template(ref_dir):
    """[(niveau, message), ...] — vérifie qu'un projet modèle est exploitable."""
    out = []
    path = os.path.join(ref_dir, "draft_content.json")
    if not os.path.exists(path):
        return [("fail", "Le projet modèle n'a pas de draft_content.json "
                         "(ouvre-le une fois dans CapCut puis ferme CapCut).")]
    try:
        draft = load_draft(path)
    except ValueError as e:
        return [("fail", str(e))]
    try:
        main = derush.find_main_track(draft)
    except RuntimeError:
        return [("fail", "Le projet modèle n'a aucun clip vidéo : mets la vidéo sur la "
                         "piste principale (un seul clip).")]
    n = len(main["segments"])
    out.append(("ok" if n == 1 else "warn",
                "Piste principale du modèle : 1 clip" if n == 1 else
                f"La piste principale du modèle contient {n} clips : il en faut UN seul (sans coupe)."))
    cw, ch = canvas_size(draft)
    out.append(("ok", f"Canvas du modèle : {int(cw)}x{int(ch)}"))
    if derush.learn_scale_keyframes(draft):
        out.append(("ok", "Exemple de keyframe d'échelle trouvé : le format de zoom "
                          "de TA version de CapCut sera copié."))
    else:
        out.append(("warn", "Aucun keyframe d'échelle d'exemple dans le modèle : les zooms risquent "
                            "de ne pas s'animer. Dans CapCut, ajoute un keyframe Échelle au début "
                            "du clip (100 %) et un à la fin (110 %)."))
    return out


# ------------------------------------------------------------------ shibis
def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return 1080, 1080


def prepare_shibi_segments(plan, rep=None):
    """Nettoie le plan : fichiers existants, pas de chevauchement sur une piste."""
    by = {"AgentOMG": [], "Marvouch": []}
    missing = set()
    for seg in plan:
        spk = poses.norm_speaker(seg.get("speaker"))
        if spk is None:
            continue
        try:
            s, e = float(seg["start_time"]), float(seg["end_time"])
            png = int(float(seg.get("png_id", 1)))
        except (KeyError, TypeError, ValueError):
            continue
        if e <= s:
            continue
        png = max(1, min(22, png))
        folder = config.ASSET_DIRS[spk]
        if not os.path.exists(os.path.join(folder, f"{png}.png")):
            missing.add(os.path.join(folder, f"{png}.png"))
            continue
        by[spk].append({"start": s, "end": e, "png": png})
    for spk, lst in by.items():
        lst.sort(key=lambda x: x["start"])
        clean, prev_end = [], 0.0
        for item in lst:
            item["start"] = max(item["start"], prev_end)
            if item["end"] - item["start"] < 0.2:
                continue
            clean.append(item)
            prev_end = item["end"]
        by[spk] = clean
    if rep:
        for m in sorted(missing):
            rep.warn(f"Image introuvable : {m}")
    return by


def _videos(draft):
    return draft["materials"].setdefault("videos", [])


def make_photo_material(draft, speaker, png, template_seg=None):
    folder = config.ASSET_DIRS[speaker]
    real_path = os.path.join(folder, f"{png}.png")
    w, h = png_size(real_path)
    mid = derush.new_id()
    mat = None
    if template_seg:
        src = next((m for m in _videos(draft) if m.get("id") == template_seg.get("material_id")), None)
        if src:
            mat = copy.deepcopy(src)
    if mat is None:
        mat = {"audio_fade": None, "category_id": "", "category_name": "local", "check_flag": 63487,
               "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0,
                        "upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0},
               "crop_ratio": "free", "crop_scale": 1.0, "local_material_id": "", "media_path": ""}
    mat.update({"id": mid, "material_id": mid, "type": "photo",
                "material_name": f"{png}.png", "path": real_path.replace("\\", "/"),
                "duration": config.PHOTO_DURATION_US, "width": w, "height": h})
    _videos(draft).append(mat)
    return mid, w, h


def shibi_clip(draft, style, speaker, png, img_w, img_h):
    """Taille + position du shibi dans le coin inférieur (formule v1 inchangée)."""
    cw, ch = canvas_size(draft)
    cfg = style.placement["face" if png in poses.BUST else "biais"]
    f = min(cw / img_w, ch / img_h)
    sc = cfg["scale"]
    half_w = f * sc * img_w / cw
    half_h = f * sc * img_h / ch
    sign = -1 if style.side.get(speaker) == "gauche" else 1
    tx = sign * (cfg["edge_x"] + half_w)
    ty = -(1 + cfg["overshoot_y"] - half_h)
    if not config.Y_AXIS_UP:
        ty = -ty
    return {"alpha": 1.0, "rotation": 0.0,
            "flip": {"horizontal": bool(style.flip.get(speaker)), "vertical": False},
            "scale": {"x": round(sc, 4), "y": round(sc, 4)},
            "transform": {"x": round(tx, 4), "y": round(ty, 4)}}


def make_shibi_segment(draft, style, base_seg, template_seg, mat, item, speaker, track_rank, index):
    mat_id, img_w, img_h = mat
    dur = int(round((item["end"] - item["start"]) * 1_000_000))
    if template_seg:
        seg = copy.deepcopy(template_seg)      # reprend position/taille du shibi du modèle
    else:
        seg = copy.deepcopy(base_seg)
        seg["clip"] = shibi_clip(draft, style, speaker, item["png"], img_w, img_h)
        seg["uniform_scale"] = {"on": True, "value": 1.0}
        seg["render_index"] = base_seg.get("render_index", 0) + track_rank
        seg["track_render_index"] = track_rank
    seg["id"] = derush.new_id()
    seg["material_id"] = mat_id
    seg["target_timerange"] = {"start": int(round(item["start"] * 1_000_000)), "duration": dur}
    seg["source_timerange"] = {"start": 0, "duration": dur}
    seg["common_keyframes"] = []
    seg["visible"] = True
    derush.clone_refs(seg, index)
    return seg


def fill_shibi_track(draft, style, track, items, speaker, base_seg, rank, index):
    """Remplit une piste avec les shibis d'un personnage. Retourne leur nombre."""
    template_seg = copy.deepcopy(track["segments"][0]) if track.get("segments") else None
    mats, new_segs = {}, []
    for item in items:
        if item["png"] not in mats:
            mats[item["png"]] = make_photo_material(draft, speaker, item["png"], template_seg)
        new_segs.append(make_shibi_segment(draft, style, base_seg, template_seg,
                                           mats[item["png"]], item, speaker, rank, index))
    track["segments"] = new_segs
    return len(new_segs)


def new_track_like(main_track):
    nt = {k: copy.deepcopy(v) for k, v in main_track.items() if k != "segments"}
    nt["id"] = derush.new_id()
    nt["segments"] = []
    return nt


def build_shibi_tracks_generate(draft, style, by_speaker, main_base):
    """Mode génération : utilise les 2 pistes vidéo supplémentaires du modèle
    (ou les crée), comme la v1."""
    main_track = derush.find_main_track(draft)
    others = [t for t in draft["tracks"] if t.get("type") == "video" and t is not main_track]
    while len(others) < 2:
        nt = new_track_like(main_track)
        draft["tracks"].append(nt)
        others.append(nt)
    index = derush.build_material_index(draft)
    counts = {}
    for rank, (speaker, track) in enumerate((("AgentOMG", others[0]), ("Marvouch", others[1])), start=1):
        counts[speaker] = fill_shibi_track(draft, style, track, by_speaker[speaker],
                                           speaker, main_base, rank, index)
    return counts


def retarget_main_video(draft, main_base, video_path, video_info, rep=None):
    """Le clip principal du modèle pointe vers SA vidéo : on vise la nôtre."""
    mat = next((m for m in _videos(draft) if m.get("id") == main_base.get("material_id")), None)
    if mat is None:
        if rep:
            rep.warn("Matériau vidéo principal introuvable dans le modèle : "
                     "le chemin de la vidéo n'a pas été mis à jour.")
        return None
    old = mat.get("path")
    mat["path"] = video_path.replace("\\", "/")
    mat["material_name"] = os.path.basename(video_path)
    mat["duration"] = int(video_info["duration"] * 1_000_000)
    mat["width"], mat["height"] = video_info["width"], video_info["height"]
    return old


def update_meta_video_path(project_dir, old, new):
    path = os.path.join(project_dir, "draft_meta_info.json")
    if not old or old == new or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    for a, b in ((old, new), (old.replace("/", "\\\\"), new.replace("/", "\\\\"))):
        text = text.replace(a, b)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
