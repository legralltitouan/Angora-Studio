"""
projedit.py — Modifier un projet CapCut DÉJÀ monté à la main.

On ne reconstruit pas la timeline : on lit les plans posés par l'utilisateur
(même dans le désordre, dupliqués, raccourcis, avec des trous), on y ajoute
des zooms animés et on calcule où chaque réplique tombe sur SA timeline.
Logique reprise de la v1 ; seule la pose des shibis est réécrite proprement
(création explicite de deux nouvelles pistes, sans bricolage sur la liste
des pistes).
"""
import os
import json
import copy
import time
import shutil
import hashlib

from . import derush, capcut

SHIBI_DIR_MARKERS = ("agentomg pose", "marvouch pose")
KEEP_REF_LISTS = {"speeds", "canvases", "sound_channel_mappings", "vocal_separations",
                  "placeholder_infos", "beats", "material_colors"}


def backup_files(project_dir, files, stamp=None):
    """Copie les draft_content.json AVANT modification."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(project_dir, "_autoshibi_backups", stamp)
    os.makedirs(dst, exist_ok=True)
    for p in files:
        rel = os.path.relpath(p, project_dir).replace(os.sep, "__")
        shutil.copy2(p, os.path.join(dst, rel))
    return dst


# ------------------------------------------------------------------ analyse de la timeline
def _video_materials(draft):
    return {m.get("id"): m for m in draft.get("materials", {}).get("videos", []) if isinstance(m, dict)}


def _tr(seg, key):
    t = seg.get(key) or {}
    try:
        return float(t["start"]) / 1e6, float(t["duration"]) / 1e6
    except (KeyError, TypeError, ValueError):
        return None


def main_track(draft):
    """Piste vidéo principale = celle qui porte le plus de vrais clips vidéo."""
    mats = _video_materials(draft)
    best, best_d = None, 0.0
    for t in draft.get("tracks", []):
        if t.get("type") != "video":
            continue
        d = 0.0
        for s in t.get("segments", []):
            m = mats.get(s.get("material_id"))
            tt = _tr(s, "target_timerange")
            if m and m.get("type") != "photo" and tt:
                d += tt[1]
        if d > best_d:
            best, best_d = t, d
    if best is None:
        raise RuntimeError("Aucun clip vidéo sur la timeline de ce projet : "
                           "pose ta vidéo sur la piste principale.")
    return best


def main_clips(draft, track=None):
    track = track or main_track(draft)
    mats = _video_materials(draft)
    clips = []
    for seg in track.get("segments", []):
        m = mats.get(seg.get("material_id"))
        if not m or m.get("type") == "photo":
            continue
        st, tt = _tr(seg, "source_timerange"), _tr(seg, "target_timerange")
        if not st or not tt or tt[1] <= 0 or st[1] <= 0:
            continue
        clips.append({"seg": seg, "path": m.get("path", ""), "src_a": st[0], "src_b": st[0] + st[1],
                      "tl_a": tt[0], "tl_b": tt[0] + tt[1], "speed": st[1] / tt[1]})
    clips.sort(key=lambda c: c["tl_a"])
    return clips


def dominant_path(clips):
    tot = {}
    for c in clips:
        tot[c["path"]] = tot.get(c["path"], 0.0) + (c["tl_b"] - c["tl_a"])
    return max(tot, key=tot.get) if tot else ""


def _is_scale_kf(k):
    return isinstance(k, dict) and "scale" in str(k.get("property_type", "")).lower()


def previous_shibi_tracks(draft, main):
    """Pistes de shibis posées par une exécution précédente."""
    mats = _video_materials(draft)
    found = []
    for t in draft.get("tracks", []):
        if t is main or t.get("type") != "video" or not t.get("segments"):
            continue
        paths = []
        for s in t["segments"]:
            m = mats.get(s.get("material_id"))
            paths.append(str((m or {}).get("path", "")).replace("\\", "/").lower())
        if paths and all(any(mk in p for mk in SHIBI_DIR_MARKERS) for p in paths):
            found.append(t)
    return found


def summarize(draft):
    track = main_track(draft)
    clips = main_clips(draft, track)
    path = dominant_path(clips)
    c = draft.get("canvas_config") or {}
    return {
        "clips": len(clips),
        "duration_s": max((c_["tl_b"] for c_ in clips), default=0.0),
        "video_path": path,
        "other_videos": len({c_["path"] for c_ in clips} - {path}),
        "speed_changed": sum(1 for c_ in clips if abs(c_["speed"] - 1.0) > 0.01),
        "with_scale_keyframes": sum(1 for c_ in clips
                                    if any(_is_scale_kf(k) for k in c_["seg"].get("common_keyframes") or [])),
        "previous_shibi_tracks": len(previous_shibi_tracks(draft, track)),
        "has_keyframe_sample": derush.learn_scale_keyframes(draft) is not None,
        "canvas": (c.get("width"), c.get("height")),
    }


def signature(clips):
    raw = json.dumps([(c["path"], round(c["src_a"], 3), round(c["src_b"], 3), round(c["tl_a"], 3))
                      for c in clips])
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def needed_ranges(clips, path=None, pad=3.0):
    """Zones de la vidéo SOURCE réellement utilisées (on ne transcrit que ça)."""
    from . import media
    path = path if path is not None else dominant_path(clips)
    return media.merge_ranges([(max(0.0, c["src_a"] - pad), c["src_b"] + pad)
                               for c in clips if c["path"] == path])


# ------------------------------------------------------------------ source -> timeline
def remap_beats(beats, clips, min_len=0.25, join_gap=0.05, rep=None):
    out = []
    for b in beats:
        try:
            s0, e0 = float(b["start_time"]), float(b["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        pieces = []
        for c in clips:
            a, z = max(s0, c["src_a"]), min(e0, c["src_b"])
            if z - a <= 1e-3:
                continue
            pieces.append([c["tl_a"] + (a - c["src_a"]) / c["speed"],
                            c["tl_a"] + (z - c["src_a"]) / c["speed"]])
        pieces.sort()
        merged = []
        for p in pieces:
            if merged and p[0] - merged[-1][1] <= join_gap:
                merged[-1][1] = max(merged[-1][1], p[1])
            else:
                merged.append(p)
        for ta, tz in merged:
            if tz - ta >= min_len:
                nb = dict(b)
                nb["start_time"], nb["end_time"] = round(ta, 3), round(tz, 3)
                out.append(nb)
    out.sort(key=lambda x: x["start_time"])
    if rep:
        rep.log(f"Recalage : {len(beats)} répliques → {len(out)} sur la timeline de ce projet.")
    return out


# ------------------------------------------------------------------ zooms sur plans existants
def apply_zoom(draft, style, seed=42, keep_existing=True, sample=None):
    """Zoom/dézoom animé sur les plans de la piste principale.
    Respecte le recadrage manuel (multiplicatif), ignore les vitesses modifiées."""
    track = main_track(draft)
    clips = main_clips(draft, track)
    sample = sample or derush.learn_scale_keyframes(draft)
    st = dict(style)
    st["zoom_base"] = 1.0
    planner = derush.ZoomPlanner(st, seed)
    stats = {"clips": len(clips), "zoom": 0, "dezoom": 0, "untouched": 0, "already": 0,
             "speed": 0, "short": 0,
             "format": "appris depuis le projet" if sample else "format générique (non vérifié)"}
    for c in clips:
        seg = c["seg"]
        if abs(c["speed"] - 1.0) > 0.01:
            stats["speed"] += 1
            continue
        kfs = seg.get("common_keyframes") or []
        old_scale = [k for k in kfs if _is_scale_kf(k)]
        if old_scale and keep_existing:
            stats["already"] += 1
            continue
        dur = int(seg["target_timerange"]["duration"])
        pick = planner.animated(dur)
        if pick is None:
            stats["short" if dur < st["zoom_min_shot_s"] * 1_000_000 else "untouched"] += 1
            continue
        s0, s1, go_out = pick
        clip = seg.get("clip") if isinstance(seg.get("clip"), dict) else None
        base = 1.0
        if clip and not old_scale:
            try:
                base = float((clip.get("scale") or {}).get("x", 1.0)) or 1.0
            except (TypeError, ValueError):
                base = 1.0
        s0, s1 = s0 * base, s1 * base
        if clip is not None:
            clip["scale"] = {"x": round(s0, 4), "y": round(s0, 4)}
        t0 = int(seg["source_timerange"]["start"])
        seg["common_keyframes"] = [k for k in kfs if not _is_scale_kf(k)] + \
            derush.make_keyframes(sample, dur, s0, s1, t0)
        stats["dezoom" if go_out else "zoom"] += 1
    return stats


# ------------------------------------------------------------------ shibis sur projet existant
def _ref_index(draft):
    idx = {}
    for key, items in draft.get("materials", {}).items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    idx[it["id"]] = key
    return idx


def max_video_render_index(draft):
    best = 0
    for t in draft.get("tracks", []):
        if t.get("type") != "video":
            continue
        for s in t.get("segments", []):
            try:
                best = max(best, int(s.get("render_index", 0)))
            except (TypeError, ValueError):
                pass
    return best


def shibi_base_segment(draft, track):
    """Segment modèle des shibis, tiré d'un clip du projet mais débarrassé de
    tout ce qui lui est propre ; il passe au-dessus de tous les plans vidéo."""
    clips = main_clips(draft, track)
    base = copy.deepcopy(clips[0]["seg"])
    idx = _ref_index(draft)
    base["common_keyframes"] = []
    base["extra_material_refs"] = [r for r in base.get("extra_material_refs", [])
                                   if idx.get(r) in KEEP_REF_LISTS]
    if "speed" in base:
        base["speed"] = 1.0
    base["render_index"] = max_video_render_index(draft)
    return base


def add_shibi_tracks(draft, style, by_speaker, main):
    """Crée DEUX nouvelles pistes de shibis au-dessus de celles de l'utilisateur
    (ses pistes, textes, effets et musiques ne sont pas touchés)."""
    base = shibi_base_segment(draft, main)
    index = derush.build_material_index(draft)
    counts, created = {}, []
    for rank, speaker in enumerate(("AgentOMG", "Marvouch"), start=1):
        track = capcut.new_track_like(main)
        counts[speaker] = capcut.fill_shibi_track(draft, style, track, by_speaker[speaker],
                                                 speaker, base, rank, index)
        if track["segments"]:
            created.append(track)
    draft["tracks"].extend(created)
    return counts


def remove_tracks(draft, tracks):
    """Supprime des pistes et les matériaux qu'elles étaient seules à utiliser."""
    gone = {id(t) for t in tracks}
    mat_ids, ref_ids = set(), set()
    for t in tracks:
        for s in t.get("segments", []):
            mat_ids.add(s.get("material_id"))
            ref_ids.update(s.get("extra_material_refs") or [])
    draft["tracks"] = [t for t in draft["tracks"] if id(t) not in gone]
    still = set()
    for t in draft["tracks"]:
        for s in t.get("segments", []):
            still.add(s.get("material_id"))
            still.update(s.get("extra_material_refs") or [])
    drop = (mat_ids | ref_ids) - still - {None, ""}
    for items in draft.get("materials", {}).values():
        if isinstance(items, list):
            items[:] = [it for it in items if not (isinstance(it, dict) and it.get("id") in drop)]
    return len(tracks)
