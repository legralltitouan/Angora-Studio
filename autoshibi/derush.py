"""
derush.py — Dérush intelligent + zooms/dézooms animés.

Principe inchangé (éprouvé en v1) : Gemini décide QUOI couper sur l'audio,
ffmpeg silencedetect décide OÙ couper au dixième de seconde. Les prompts,
règles par niveau, tables de zooms et le planificateur de zooms sont repris
à l'identique ; seuls l'exécution (parallèle, caches, recherche des silences
par dichotomie) et le reporting changent.
"""
import os
import re
import copy
import json
import uuid
import random
import bisect

from . import config, media, gemini as gm
from .events import Cancelled

# ------------------------------------------------------------------ paramètres
DEFAULTS = {
    "max_pause_s": 0.6,        # une pause plus longue que ça est raccourcie
    "pause_keep_s": 0.25,      # durée de pause conservée après raccourcissement
    "min_keep_s": 0.5,         # un plan conservé plus court que ça est supprimé
    "max_shot_s": 7.0,         # un plan plus long est découpé dans un silence
    "derush_level": 3,         # 1 léger / 2 normal / 3 serré
    "zoom_base": 1.0,
    "zoom_intensity": 1.0,     # 1.0 = 100->110 % ; 0.5 = moitié ; 2.0 = double
    "zoom_in": [[1.00, 1.10], [1.00, 1.05], [1.00, 1.07], [1.03, 1.10],
                [1.02, 1.08], [1.05, 1.12], [1.04, 1.09]],
    "zoom_out": [[1.10, 1.00], [1.10, 1.05], [1.07, 1.00], [1.05, 1.00],
                 [1.08, 1.02], [1.12, 1.05], [1.09, 1.03]],
    "zoom_out_ratio": 0.45,
    "zoom_static": [1.00, 1.00, 1.04, 1.07],
    "zoom_coverage": 0.80,
    "zoom_flip_prob": 0.70,
    "zoom_min_shot_s": 1.0,
    "style_notes": "",
}
END_MARGIN_US = 33_333
SNAP_WINDOW = 1.5
MAX_CUT_S = 240
CUT_MARGIN_S = 0.5


def new_id():
    return str(uuid.uuid4()).upper()


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def load_style(path=config.STYLE_PATH):
    style = dict(DEFAULTS)
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                style.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except (OSError, json.JSONDecodeError):
            pass
    return style


# ------------------------------------------------------------------ profil de style
STYLE_PROMPT = """Tu es un monteur vidéo YouTube expérimenté. Analyse le MONTAGE de cette vidéo
(rythme, coupes), pas son sujet. Réponds en JSON pur, sans texte autour :
{
  "avg_shot_duration_s": <durée moyenne d'un plan en secondes>,
  "max_pause_kept_s": <plus longue pause de parole habituellement conservée, en secondes>,
  "removes": "<ce qui est systématiquement coupé>",
  "keeps": "<ce qui est toujours gardé : réactions, rires, blagues...>",
  "notes": "<2-3 phrases sur le rythme et le style de montage>"
}"""


def _yt_url(url):
    m = re.search(r"(?:youtu\.be/|v=)([\w-]{11})", url)
    if not m:
        raise ValueError(f"URL YouTube invalide : {url}")
    return f"https://www.youtube.com/watch?v={m.group(1)}"


def build_style_profile(runner, urls, out_path, rep):
    from google.genai import types
    results = []
    for url in urls:
        tid = rep.task_start("gemini", f"Analyse du style : {url}")
        try:
            part = types.Part(file_data=types.FileData(file_uri=_yt_url(url)))
            contents = types.Content(parts=[part, types.Part(text=STYLE_PROMPT)])
            data = runner.generate_json(contents, kind="object", tid=tid,
                                        label="Style", temperature=0.3)
            rep.task_end(tid, ok=data is not None)
        except Cancelled:
            rep.task_end(tid, ok=False)
            raise
        except Exception:
            rep.task_end(tid, ok=False)
            data = None
        if data:
            results.append(data)
        else:
            rep.warn(f"Style : échec pour {url} (vidéo privée ? trop longue ?).")
    if not results:
        rep.warn("Aucun profil de style obtenu : paramètres par défaut utilisés.")
        return None

    def avg(key, default):
        vals = []
        for d in results:
            try:
                vals.append(float(d[key]))
            except (KeyError, TypeError, ValueError):
                pass
        return sum(vals) / len(vals) if vals else default

    profile = {
        "max_shot_s": round(_clamp(avg("avg_shot_duration_s", 4.0) * 1.6, 3.0, 12.0), 2),
        "max_pause_s": round(_clamp(avg("max_pause_kept_s", 0.6), 0.3, 1.2), 2),
        "style_notes": " | ".join(
            f"coupe: {d.get('removes', '')} ; garde: {d.get('keeps', '')} ; {d.get('notes', '')}"
            for d in results),
        "raw": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=4, ensure_ascii=False)
    rep.log(f"Profil de style sauvegardé ({len(results)} vidéo(s) de référence).")
    return profile


# ------------------------------------------------------------------ coupes
LEVEL_RULES = {
    1: """- hors-sujet technique : micro, launcher, installation, adresse IP, "tu m'entends ?", "attends je relance"
- faux départs et phrases recommencées (garde la dernière prise)
- longs blancs sans parole : plus de 4 secondes""",
    2: """- hors-sujet technique : micro, launcher, installation, adresse IP, "tu m'entends ?", "attends je relance"
- faux départs, phrases recommencées, répétitions (garde la meilleure prise)
- hésitations longues, "euh" prolongés
- temps morts : plus de 2,5 secondes sans parole, juste du bruit de jeu""",
    3: """- hors-sujet technique : micro, launcher, installation, adresse IP, "tu m'entends ?", "attends je relance"
- faux départs, phrases recommencées, répétitions (garde la meilleure prise)
- hésitations, "euh", bégaiements, silences qui traînent
- temps morts : dès 1,5 seconde sans parole utile (bruit de jeu, déplacements silencieux, saisie de commandes)
- tout passage sans intérêt : bavardage qui n'apporte ni rire, ni information, ni réaction ; commentaire
  plat de ce qu'ils font ; explications qui traînent
- OBJECTIF : une vidéo ULTRA rythmée, comme les meilleurs YouTubers : environ un quart à un tiers de
  l'extrait doit être supprimé""",
}

DERUSH_PROMPT = """Tu es monteur vidéo YouTube. Voici l'audio brut d'un enregistrement de deux joueurs Minecraft
(AgentOMG et Marvouch). Style de montage visé : __STYLE__
Niveau de coupe : __LEVEL__ sur 3.

Découpe TOUT l'extrait en segments consécutifs (de 1 à 15 secondes, sans trou) et décide pour CHACUN :
garder (keep=true) ou supprimer (keep=false).

À SUPPRIMER :
__RULES__

À GARDER TOUJOURS : blagues, rires, cris, réactions, moments marquants, informations utiles à l'histoire,
même brefs. Ne coupe jamais au milieu d'une phrase que tu gardes.
Timestamps en secondes depuis le début de CET extrait, précision 0.1 s.

Réponds en JSON pur (tableau couvrant tout l'extrait) :
[{"start": 0.0, "end": 4.2, "keep": true, "why": ""}, {"start": 4.2, "end": 9.8, "keep": false, "why": "temps mort"}]"""


def _derush_prompt(style, level):
    return (DERUSH_PROMPT.replace("__STYLE__", style.get("style_notes") or "rythmé, dynamique, sans temps mort")
            .replace("__LEVEL__", str(level)).replace("__RULES__", LEVEL_RULES[level]))


def _is_cut(c):
    return isinstance(c, dict) and c.get("keep") in (False, "false", "False", 0, "non", "no")


class SilenceSnap:
    """Recale un instant sur le silence le plus proche — recherche par dichotomie
    (la v1 balayait tous les silences à chaque coupe : O(n²) sur les longues vidéos)."""

    def __init__(self, silences):
        self.silences = silences
        self.starts = [s for s, _ in silences]

    def snap(self, t, pause_keep, mode):
        if not self.silences:
            return None
        i = bisect.bisect_left(self.starts, t)
        best, best_d = None, SNAP_WINDOW + 1
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(self.silences):
                s, e = self.silences[j]
                d = 0.0 if s <= t <= e else min(abs(t - s), abs(t - e))
                if d < best_d:
                    best, best_d = (s, e), d
        if best is None or best_d > SNAP_WINDOW:
            return None
        s, e = best
        margin = min(pause_keep / 2, (e - s) / 2)
        return s + margin if mode == "start" else e - margin


def _trim_long_pauses(silences, style):
    removes = []
    for s, e in silences:
        if e - s > style["max_pause_s"]:
            m = style["pause_keep_s"] / 2
            removes.append((s + m, e - m))
    return removes


def _split_long(keep, silences, max_shot):
    mids = [(s + e) / 2 for s, e in silences]
    out = []
    for a, b in keep:
        cur = a
        while b - cur > max_shot * 1.3:
            target = cur + max_shot
            lo = bisect.bisect_left(mids, cur + max_shot * 0.5)
            hi = bisect.bisect_right(mids, min(cur + max_shot * 1.3, b - 1.0))
            cands = mids[lo:hi]
            if not cands:
                break
            m = min(cands, key=lambda x: abs(x - target))
            out.append((cur, m))
            cur = m
        out.append((cur, b))
    return out


def plan_cuts(chunks, cache, silences, style, total, rep):
    """À partir des réponses Gemini (cache par tranche), calcule les plans à
    conserver. Aucun appel réseau ici : 100 % déterministe, donc rejouable."""
    snapper = SilenceSnap(silences)
    by_index = {c.index: c for c in chunks}
    raw_cuts = []
    for i_str, data in cache["chunks"].items():
        ch = by_index.get(int(i_str))
        if ch is None:
            continue
        for c in data or []:
            if not _is_cut(c):
                continue
            try:
                a = _clamp(float(c["start"]) + ch.offset, 0.0, total)
                b = _clamp(float(c["end"]) + ch.offset, 0.0, total)
            except (KeyError, TypeError, ValueError):
                continue
            raw_cuts.append((a, b, str(c.get("why", c.get("reason", "")))))

    removes, n_sil, n_margin, ignored = [], 0, 0, []
    for a, b, reason in sorted(raw_cuts):
        if b - a < 0.8:
            continue
        if b - a > MAX_CUT_S:
            ignored.append((a, b, "trop longue", reason))
            continue
        a2 = snapper.snap(a, style["pause_keep_s"], "start")
        b2 = snapper.snap(b, style["pause_keep_s"], "end")
        if a2 is None and b2 is None:
            n_margin += 1
        else:
            n_sil += 1
        a2 = a + CUT_MARGIN_S if a2 is None else a2
        b2 = b - CUT_MARGIN_S if b2 is None else b2
        if b2 - a2 < 0.5:
            ignored.append((a, b, "trop courte après marge", reason))
            continue
        removes.append((a2, b2))
    gem = media.merge_ranges(removes)
    gem_len = sum(y - x for x, y in gem)
    removes += _trim_long_pauses(silences, style)
    rep.log(f"Coupes Gemini : {len(raw_cuts)} proposées → {len(gem)} appliquées "
            f"({n_sil} calées sur un silence, {n_margin} avec marge de sécurité), "
            f"{len(ignored)} ignorées, {gem_len:.0f} s retirées.")
    for a, b, why, reason in ignored[:6]:
        rep.log(f"· coupe ignorée ({why}) {a:.1f}–{b:.1f} s : {reason[:70]}")
    pause_len = sum(y - x for x, y in media.merge_ranges(removes)) - gem_len
    rep.log(f"Pauses raccourcies (> {style['max_pause_s']} s) : ~{max(0, pause_len):.0f} s retirées.")

    keep = [(a, b) for a, b in media.subtract_ranges(total, removes) if b - a >= style["min_keep_s"]]
    keep = _split_long(keep, silences, style["max_shot_s"])
    return keep


def run_derush(runner, video, chunks, silences, style, cache_dir, rep, stage="derush", cut=True):
    """Envoie les tranches du mix à Gemini (en parallèle, cache par tranche),
    puis calcule les plans conservés [(début, fin), ...] en temps source."""
    total = media.probe_duration(video)

    if not cut:
        removes = []
        keep = [(a, b) for a, b in media.subtract_ranges(total, removes)
                if b - a >= style["min_keep_s"]]
        keep = _split_long(keep, silences, style["max_shot_s"])
        kept = sum(b - a for a, b in keep)
        rep.log(f"Dérush désactivé : tout est gardé ({len(keep)} plans pour les zooms).", stage=stage)
        return keep

    level = int(_clamp(int(style.get("derush_level", 3)), 1, 3))
    prompt = _derush_prompt(style, level)
    cache_file = os.path.join(cache_dir, "derush_chunks.json")
    cache = gm.load_chunk_cache(cache_file, gm.prompt_key(prompt))
    todo = [c for c in chunks if c.duration >= media.MIN_CHUNK_S and str(c.index) not in cache["chunks"]]
    rep.stage_units(stage, len(chunks))
    for _ in range(len(chunks) - len(todo)):
        rep.stage_tick(stage)
    rep.log(f"{len(chunks)} tranches audio : {len(todo)} à analyser "
            f"({len(chunks) - len(todo)} déjà en cache), niveau {level}/3, "
            f"{runner.workers} en parallèle.", stage=stage)

    def work(chunk):
        tid = rep.task_start("gemini", f"Dérush · tranche {chunk.index + 1}/{len(chunks)}")
        try:
            f = runner.upload(chunk.path, tid, f"Tranche {chunk.index + 1}")
            try:
                return runner.generate_json([f, prompt], "array", tid=tid,
                                            label=f"Tranche {chunk.index + 1}", temperature=0.2)
            finally:
                runner.delete(f)
        finally:
            rep.task_end(tid)

    def done(chunk, data):
        if data is None:
            rep.warn(f"Tranche {chunk.index + 1}/{len(chunks)} : pas de réponse exploitable "
                     "(retentée au prochain lancement, le reste est en cache).", stage=stage)
        else:
            cache["chunks"][str(chunk.index)] = data
            gm.save_chunk_cache(cache_file, cache)
            rep.log(f"Tranche {chunk.index + 1}/{len(chunks)} : {len(data)} segments, "
                    f"{sum(1 for c in data if _is_cut(c))} à supprimer.", stage=stage)
        rep.stage_tick(stage, label=f"Tranche {chunk.index + 1}/{len(chunks)} analysée")

    err = runner.run_parallel(todo, work, on_done=done)
    if err is not None and not cache["chunks"]:
        raise err

    keep = plan_cuts(chunks, cache, silences, style, total, rep)
    kept = sum(b - a for a, b in keep)
    rep.ok(f"Dérush : {total:.0f} s → {kept:.0f} s ({len(keep)} plans, "
           f"{100 * (1 - kept / total) if total else 0:.0f} % supprimé).", stage=stage)
    return keep


# ------------------------------------------------------------------ remapping source -> timeline
def build_map(keep):
    m, cursor = [], 0.0
    for a, b in keep:
        m.append((a, b, cursor))
        cursor += b - a
    return m


def remap_segments(segments, keep, min_len=0.25, rep=None):
    m = build_map(keep)
    starts = [a for a, _, _ in m]

    def map_start(t):
        i = bisect.bisect_right(starts, t)
        for a, b, tl in m[max(0, i - 1):]:
            if t < b:
                return tl + max(t, a) - a
        return None

    def map_end(t):
        for a, b, tl in reversed(m):
            if t > a:
                return tl + min(t, b) - a
        return None

    out = []
    for seg in segments:
        try:
            s0, e0 = float(seg["start_time"]), float(seg["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        ns, ne = map_start(s0), map_end(e0)
        if ns is None or ne is None or ne - ns < min_len:
            continue
        s = dict(seg)
        s["start_time"], s["end_time"] = round(ns, 3), round(ne, 3)
        out.append(s)
    if rep:
        rep.log(f"Recalage sur la timeline dérushée : {len(segments)} → {len(out)} segments.")
    return out


# ------------------------------------------------------------------ keyframes d'échelle
def learn_scale_keyframes(draft):
    """Format EXACT des keyframes d'échelle de TA version de CapCut, copié
    depuis un segment qui en contient déjà (créé par CapCut lui-même)."""
    for t in draft.get("tracks", []):
        for s in t.get("segments", []):
            kfs = s.get("common_keyframes") or []
            scale = [k for k in kfs if "scale" in str(k.get("property_type", "")).lower()]
            if scale:
                return copy.deepcopy(scale)
    return None


def keyframes_from_sample(sample, dur, s0, s1, t0=0):
    out = []
    for lst in sample:
        new = copy.deepcopy(lst)
        new["id"] = new_id()
        pts = new.get("keyframe_list") or []
        if not pts:
            continue
        first = pts[0]
        last = copy.deepcopy(pts[-1] if len(pts) > 1 else pts[0])
        end_off = max(1, dur - END_MARGIN_US)
        for p, off, v in ((first, t0, s0), (last, t0 + end_off, s1)):
            p["id"] = new_id()
            p["time_offset"] = off
            p["values"] = [round(v, 4)] * max(1, len(p.get("values") or [v]))
        new["keyframe_list"] = [first, last]
        out.append(new)
    return out


def fallback_keyframes(dur, s0, s1, t0=0):
    """Format générique si aucun exemple n'existe dans le projet modèle."""
    def kf(prop):
        def pt(offset, value):
            return {"curveType": "Line", "graphicCurveType": "linear", "id": new_id(),
                    "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0},
                    "string_value": "", "time_offset": offset, "values": [round(value, 4)]}
        return {"id": new_id(), "keyframe_list": [pt(t0, s0), pt(t0 + max(1, dur - END_MARGIN_US), s1)],
                "material_id": "", "property_type": prop}
    return [kf("KFTypeScaleX"), kf("KFTypeScaleY")]


def make_keyframes(sample, dur, s0, s1, t0=0):
    return (keyframes_from_sample(sample, dur, s0, s1, t0) if sample
            else fallback_keyframes(dur, s0, s1, t0))


class ZoomPlanner:
    """Tire, plan après plan, un zoom / dézoom animé (ou rien).
    Algorithme repris à l'identique de la v1 (alternance, intensité, graine)."""

    def __init__(self, style, seed=42):
        self.style = style
        self.rng = random.Random(seed)
        self.zin = [tuple(p) for p in style["zoom_in"]]
        self.zout = [tuple(p) for p in style["zoom_out"]]
        self.zb = style["zoom_base"]
        self.intensity = max(0.0, float(style.get("zoom_intensity", 1.0)))
        self.prev = None
        self.n_in = 0
        self.n_out = 0

    def static(self):
        return self.zb * self.rng.choice(self.style["zoom_static"])

    def animated(self, dur_us):
        st, rng = self.style, self.rng
        if not (dur_us >= st["zoom_min_shot_s"] * 1_000_000 and rng.random() < st["zoom_coverage"]):
            return None
        prev = self.prev
        if prev is not None and rng.random() < st["zoom_flip_prob"]:
            go_out = not prev[2]
        else:
            go_out = rng.random() < st["zoom_out_ratio"]
        pool_src = self.zout if go_out else self.zin
        pool = [p for p in pool_src if p != prev[:2]] if prev else pool_src
        pat = rng.choice(pool or pool_src)
        k = _clamp((dur_us / 1e6) / 4.0, 0.6, 1.0)
        i = self.intensity
        p0, p1 = 1 + (pat[0] - 1) * i, 1 + (pat[1] - 1) * i
        s0 = self.zb * p0
        s1 = self.zb * (p0 + (p1 - p0) * k)
        self.prev = (pat[0], pat[1], go_out)
        if go_out:
            self.n_out += 1
        else:
            self.n_in += 1
        return s0, s1, go_out


# ------------------------------------------------------------------ matériaux annexes
def build_material_index(draft):
    """id -> (liste, objet) pour cloner les matériaux annexes en O(1)
    (la v1 rebalayait tous les matériaux pour chaque segment)."""
    idx = {}
    for items in draft.get("materials", {}).values():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    idx[it["id"]] = (items, it)
    return idx


def clone_refs(seg, index):
    """Donne au segment ses propres matériaux annexes (vitesse, canvas...),
    comme CapCut le fait quand on duplique un clip."""
    new_refs = []
    for ref in seg.get("extra_material_refs", []):
        hit = index.get(ref)
        if hit:
            items, it = hit
            c = copy.deepcopy(it)
            c["id"] = new_id()
            items.append(c)
            new_refs.append(c["id"])
        else:
            new_refs.append(ref)
    seg["extra_material_refs"] = new_refs


def find_main_track(draft):
    for t in draft["tracks"]:
        if t.get("type") == "video" and t.get("segments"):
            return t
    raise RuntimeError("Aucune piste vidéo avec un segment dans ce draft "
                       "(la piste principale doit contenir la vidéo).")


def apply_keep_to_draft(draft, keep, style, zoom=True, seed=42, rep=None):
    """Remplace le plan unique de la piste principale par un plan par segment
    conservé, avec zooms animés. Retourne (segment modèle nettoyé, mode)."""
    track = find_main_track(draft)
    base = track["segments"][0]
    base_src0 = base.get("source_timerange", {}).get("start", 0)
    sample = learn_scale_keyframes(draft) if zoom else None
    mode = "appris depuis le modèle" if sample else "format générique (non vérifié)"
    index = build_material_index(draft)

    main_base = copy.deepcopy(base)
    main_base["common_keyframes"] = []

    planner = ZoomPlanner(style, seed)
    new_segments, cursor = [], 0
    for a, b in keep:
        dur = int(round((b - a) * 1_000_000))
        seg = copy.deepcopy(base)
        seg["id"] = new_id()
        seg["source_timerange"] = {"start": base_src0 + int(round(a * 1_000_000)), "duration": dur}
        seg["target_timerange"] = {"start": cursor, "duration": dur}
        seg["common_keyframes"] = []
        static = planner.static()
        if isinstance(seg.get("clip"), dict):
            seg["clip"]["scale"] = {"x": static, "y": static}
        clone_refs(seg, index)
        pick = planner.animated(dur) if zoom else None
        if pick:
            s0, s1, _ = pick
            if isinstance(seg.get("clip"), dict):
                seg["clip"]["scale"] = {"x": s0, "y": s0}
            # CapCut lit les temps des keyframes dans le temps de la vidéo
            # SOURCE (vérifié par test en v1) : décalage par le début du plan.
            t0 = seg["source_timerange"]["start"]
            seg["common_keyframes"] = make_keyframes(sample, dur, s0, s1, t0)
        new_segments.append(seg)
        cursor += dur

    track["segments"] = new_segments
    draft["duration"] = cursor
    if rep:
        if any(t.get("type") == "audio" and t.get("segments") for t in draft["tracks"]):
            rep.warn("Le modèle contient des pistes audio séparées : elles ne sont pas recoupées par le dérush.")
        rep.log(f"Piste principale : {len(new_segments)} plans, {cursor / 1e6:.1f} s, "
                f"{planner.n_in} zooms + {planner.n_out} dézooms (keyframes : {mode}).")
    return main_base, mode


def dump_debug(draft, path):
    info = {"top_level_keys": sorted(draft.keys()),
            "materials": {k: len(v) for k, v in draft.get("materials", {}).items() if isinstance(v, list)},
            "tracks": []}
    for i, t in enumerate(draft.get("tracks", [])):
        segs = t.get("segments", [])
        info["tracks"].append({"index": i, "type": t.get("type"), "keys": sorted(t.keys()),
                               "n_segments": len(segs), "first_segment": segs[0] if segs else None})
    vids = draft.get("materials", {}).get("videos", [])
    info["first_photo_material"] = next((m for m in vids if m.get("type") == "photo"), None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
