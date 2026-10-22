"""
poses.py — Choix intelligent des poses shibi.

Catalogue, règles de secours (émotion/intention), découpage en beats et
anti-répétition : identiques à la v1 (résultat équivalent). Changement v2 :
les lots de 30 répliques partent EN PARALLÈLE (la v1 les enchaînait un par
un). La variété reste garantie de toute façon par l'assignation déterministe
finale (_assign), qui interdit de répéter une pose récente du personnage ;
chaque lot reçoit comme contexte les répliques précédentes avec la pose que
les règles de secours auraient choisie.
"""
import os
import re
import json
import math
import hashlib
import unicodedata

from . import gemini as gm

# ------------------------------------------------------------------ catalogue (inchangé)
CATALOG = {
    1:  ("buste, bras le long du corps, neutre (pose de base)", 1),
    2:  ("buste, bras légèrement ouverts vers le bas : explique calmement, accueillant", 1),
    3:  ("buste, un bras replié devant lui, poing vers la caméra : interpelle, affirme, désigne le spectateur", 2),
    4:  ("buste, main au menton/joue, un bras levé : réfléchit, doute, hésite", 1),
    5:  ("buste, les deux mains à côté de la tête : panique, choc, 'oh non', incrédulité", 3),
    6:  ("buste, bras écartés vers le bas, épaules larges : haussement d'épaules, 'bah quoi ?', incompréhension", 2),
    7:  ("gros plan, tête baissée vers l'avant : dépité, honteux, soupir, dégoûté, ou regard menaçant", 2),
    8:  ("buste, main levée devant le visage : gêné, main sur la bouche, facepalm, rire étouffé", 2),
    9:  ("buste, bras tendus à l'horizontale : grand geste, 'voilà !', présentation, 'mais quoi ?!'", 3),
    10: ("gros plan du visage, bouche ouverte en 'o' : surpris, crie, parle fort", 3),
    11: ("gros plan, bras flous écartés (mouvement) : s'agite, fou rire, énervé, chaos", 3),
    12: ("buste, les deux bras levés : joie, victoire, hype", 3),
    13: ("plein pied, un bras tendu sur le côté : montre/désigne quelque chose", 2),
    14: ("plein pied, un bras levé au-dessus de la tête, penché : salue de loin, cherche du regard, fait signe", 2),
    15: ("plein pied, penché, une jambe levée : action, fuite, trébuche, coup de pied", 3),
    16: ("plein pied, une main levée près de la tête : salut, 'hey', attire l'attention", 2),
    17: ("plein pied, droit, bras repliés contre lui : calme, attend, écoute, neutre", 1),
    18: ("plein pied, bras légèrement écartés : haussement d'épaules, incertain, 'je sais pas'", 1),
    19: ("plein pied, les deux bras en l'air : célèbre, victoire, grosse joie", 3),
    20: ("plein pied, voûté/recroquevillé, tête baissée : triste, effrayé, se cache, blessé, fatigué", 1),
    21: ("plein pied, bras croisés : pas impressionné, sceptique, arrogant, patient", 1),
    22: ("plein pied, jambes écartées, bras en travers (défi) : se moque, sarcastique, attitude, 'dab'", 2),
}
ALL_POSES = list(CATALOG.keys())
ENERGY = {k: v[1] for k, v in CATALOG.items()}
BUST = set(range(1, 13))

MAX_BEAT = 3.0
MIN_PART = 0.9
WORDS_PER_S = 2.8
BATCH = 30


def _n(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


_EMOTION = {
    "neutre": [1, 2, 17, 21, 4], "calme": [1, 17, 2], "joyeux": [12, 19, 9, 2], "excité": [12, 19, 9, 11, 5],
    "amusé": [8, 11, 22, 6, 2], "surpris": [10, 5, 9], "choqué": [5, 10, 8], "peur": [20, 5, 10],
    "panique": [5, 11, 15, 10], "colère": [7, 3, 22, 9, 11], "énervé": [7, 9, 3, 22], "dégoûté": [7, 6, 8, 21],
    "triste": [20, 7, 1], "déçu": [7, 20, 6], "gêné": [8, 4, 6], "curieux": [4, 2, 13, 3],
    "réfléchi": [4, 1, 17], "confiant": [21, 22, 3, 2], "sarcastique": [22, 21, 3, 6], "fier": [21, 12, 9],
    "fatigué": [20, 7, 1],
}
_INTENT = {
    "salut": [16, 14, 2], "question": [4, 3, 6], "victoire": [12, 19, 9], "danger": [15, 5, 20],
    "blague": [8, 22, 11, 6], "ordre": [3, 13, 22], "doute": [4, 6, 18], "remerciement": [2, 16, 1],
    "echec": [7, 20, 6], "insulte": [22, 7, 3],
}
EMOTION_FALLBACK = {_n(k): v for k, v in _EMOTION.items()}
INTENT_FALLBACK = {_n(k): v for k, v in _INTENT.items()}


def norm_speaker(s):
    s = str(s or "").lower()
    if "omg" in s:
        return "AgentOMG"
    if "marv" in s:
        return "Marvouch"
    return None


# ------------------------------------------------------------------ découpage en beats
def _group(parts, n):
    total = sum(len(p) for p in parts) or 1
    target, groups, cur, acc = total / n, [], [], 0
    for p in parts:
        cur.append(p)
        acc += len(p)
        if acc >= target * (len(groups) + 1) and len(groups) < n - 1:
            groups.append(" ".join(cur))
            cur = []
    if cur:
        groups.append(" ".join(cur))
    return groups


def _split_text(text, n):
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p]
    if len(parts) < n:
        parts = [q for p in parts for q in re.split(r"(?<=[,;:])\s+", p) if q]
    if len(parts) < n:
        words = text.split()
        step = max(1, math.ceil(len(words) / n))
        parts = [" ".join(words[i:i + step]) for i in range(0, len(words), step)]
    return _group(parts, n) if parts else [text]


def refine_segments(segments, rep=None):
    beats = []
    for seg in segments:
        raw = str(seg.get("speaker", "")).lower()
        if "both" in raw or "deux" in raw:
            spks = ["AgentOMG", "Marvouch"]
        else:
            spk0 = norm_speaker(seg.get("speaker"))
            spks = [spk0] if spk0 else []
        for spk in spks:
            _add_beats(beats, seg, spk)
    beats.sort(key=lambda b: b["start_time"])
    if rep:
        avg = sum(b["end_time"] - b["start_time"] for b in beats) / max(1, len(beats))
        both = sum(1 for s_ in segments if "both" in str(s_.get("speaker", "")).lower())
        rep.log(f"Beats : {len(segments)} répliques → {len(beats)} beats courts "
                f"(durée moyenne {avg:.1f} s, dont {both} en simultané).")
    return beats


def _add_beats(beats, seg, spk):
    try:
        s, e = float(seg["start_time"]), float(seg["end_time"])
    except (KeyError, TypeError, ValueError):
        return
    if e <= s:
        return
    text = str(seg.get("text", "")).strip()
    words = len(text.split())
    if words and not seg.get("exact"):
        allowed = max(1.2, words / WORDS_PER_S * 1.5 + 0.8)
        if e - s > allowed + 1.5:
            e = s + allowed
    dur = e - s
    n = 1 if dur <= MAX_BEAT * 1.25 else math.ceil(dur / MAX_BEAT)
    n = max(1, min(n, int(dur / MIN_PART) or 1))
    texts = _split_text(text, n) if (n > 1 and text) else [text] * n
    n = len(texts)
    if text and n > 1:
        lens = [max(1, len(t)) for t in texts]
        cum, bounds = 0, [s]
        for L in lens:
            cum += L
            bounds.append(s + dur * cum / sum(lens))
    else:
        bounds = [s + dur * k / n for k in range(n + 1)]
    for k in range(n):
        beats.append({"start_time": round(bounds[k], 3), "end_time": round(bounds[k + 1], 3),
                      "speaker": spk, "text": texts[k],
                      "emotion": seg.get("emotion", "neutre"),
                      "intensity": seg.get("intensity", 1), "intent": seg.get("intent", "")})


def add_linger(plan, linger):
    if linger <= 0:
        return plan
    out = []
    for spk in ("AgentOMG", "Marvouch"):
        items = sorted((dict(p) for p in plan if p["speaker"] == spk), key=lambda p: p["start_time"])
        for i, p in enumerate(items):
            limit = items[i + 1]["start_time"] if i + 1 < len(items) else float("inf")
            p["end_time"] = round(min(p["end_time"] + linger, limit), 3)
        out += items
    return sorted(out, key=lambda p: p["start_time"])


# ------------------------------------------------------------------ choix des poses
PICK_PROMPT = """Tu es monteur vidéo. Deux streamers Minecraft discutent. Pendant chaque réplique, on affiche
une illustration ("shibi") du personnage qui parle, dans un coin de l'écran.
Choisis pour CHAQUE réplique la pose la plus expressive et la plus adaptée, en tenant compte du texte,
de l'émotion, de l'intensité ET du contexte (répliques d'avant et d'après).

Poses disponibles (les mêmes pour les deux personnages) :
__CATALOG__

Règles :
- Colle au sens réel de la phrase : rire/blague -> poses de rire ou de moquerie ; victoire -> bras levés ;
  réflexion/doute -> main au menton ; salut -> main levée ; incompréhension -> haussement d'épaules ;
  surprise -> gros plan bouche ouverte ; danger/peur -> panique ou recroquevillé ; fierté/défi -> bras croisés.
- Réplique calme -> poses calmes (1, 2, 17, 21...). Grosse réaction -> poses énergiques (5, 9, 10, 11, 12, 19...).
- Utilise TOUT le catalogue. Varie : ne reprends jamais une pose déjà utilisée sur les 3 répliques
  précédentes du même personnage. Mélange gros plans (1-12) et plein pied (13-22).
- Si les deux personnages parlent en même temps (même horaire), donne-leur des poses DIFFÉRENTES et complémentaires.
- Pour chaque réplique, donne 3 poses distinctes, de la meilleure à la troisième.

Déjà attribué juste avant :
__PREVIOUS__

Répliques à traiter (index | personnage | durée | émotion (intensité) / intention | texte) :
__LINES__

Réponds en JSON pur : [{"i": 0, "png_ids": [12, 19, 9]}, ...] (une entrée par index, rien d'autre)"""


def _unique(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _intensity(b):
    try:
        return max(1, min(3, int(float(b.get("intensity", 1)))))
    except (TypeError, ValueError):
        return 1


def heuristic_candidates(b):
    inten = _intensity(b)
    cands = INTENT_FALLBACK.get(_n(b.get("intent")), [])[:2] + EMOTION_FALLBACK.get(_n(b.get("emotion")), [1, 2, 17])
    rest = sorted(ALL_POSES, key=lambda p: (abs(ENERGY[p] - inten), p))
    return _unique(cands + rest)


def _ask_candidates(runner, beats, rep, stage, cache_file=None, key=None, force=False):
    """Candidats Gemini par beat, lots de 30 EN PARALLÈLE, cache par lot."""
    cands, saved = {}, {}
    if cache_file and not force and os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("key") == key:
                saved = data.get("batches", {})
        except (OSError, json.JSONDecodeError):
            pass
    catalog = "\n".join(f"{i}: {d}" for i, (d, _) in CATALOG.items())
    starts = list(range(0, len(beats), BATCH))
    rep.stage_units(stage, len(starts))
    todo = []
    for start in starts:
        if str(start) in saved:
            for i_str, ids in saved[str(start)].items():
                cands[int(i_str)] = ids
            rep.stage_tick(stage, label=f"Poses {start + 1}–{min(start + BATCH, len(beats))} : déjà obtenues (reprise)")
        else:
            todo.append(start)
    if todo:
        rep.log(f"Poses : {len(todo)} lot(s) de {BATCH} répliques à demander à Gemini "
                f"({runner.workers} en parallèle).", stage=stage)

    def work(start):
        chunk = beats[start:start + BATCH]
        label = f"Poses {start + 1}–{start + len(chunk)}/{len(beats)}"
        tid = rep.task_start("gemini", label)
        try:
            prev_lines = []
            for j in range(max(0, start - 4), start):
                assumed = (cands.get(j) or heuristic_candidates(beats[j]))[:1]
                if assumed:
                    prev_lines.append(f'- {beats[j]["speaker"]} : "{beats[j]["text"][:70]}" -> pose {assumed[0]}')
            lines = "\n".join(
                f'{start + k} | {b["speaker"]} | {b["end_time"] - b["start_time"]:.1f}s | '
                f'{b.get("emotion", "?")} ({b.get("intensity", "?")}) / {b.get("intent", "") or "?"} | "{b.get("text", "")}"'
                for k, b in enumerate(chunk))
            prompt = (PICK_PROMPT.replace("__CATALOG__", catalog)
                      .replace("__PREVIOUS__", "\n".join(prev_lines) or "(début de la vidéo)")
                      .replace("__LINES__", lines))
            data = runner.generate_json(prompt, "array", tid=tid, label=label, temperature=0.5)
            got = {}
            for d in data or []:
                try:
                    i = int(d["i"])
                    ids = _unique([int(x) for x in d["png_ids"] if 1 <= int(x) <= 22])
                except (KeyError, TypeError, ValueError):
                    continue
                if ids:
                    got[i] = ids
            return got
        finally:
            rep.task_end(tid)

    def done(start, got):
        chunk_len = len(beats[start:start + BATCH])
        got = got or {}
        cands.update(got)
        rep.log(f"Poses {start + 1}–{start + chunk_len}/{len(beats)} : {len(got)} choix Gemini"
                + ("" if len(got) == chunk_len else " (le reste : règles de secours)"), stage=stage)
        rep.stage_tick(stage, label=f"Poses jusqu'à la réplique {start + chunk_len}/{len(beats)}")
        if got and cache_file:
            saved[str(start)] = {str(k): v for k, v in got.items()}
            gm.save_chunk_cache(cache_file, {"key": key, "batches": saved})

    runner.run_parallel(todo, work, on_done=done)
    return cands


def _assign(beats, cands, max_hold):
    recent = {"AgentOMG": [], "Marvouch": []}
    plan = []
    for i, b in enumerate(beats):
        spk = b["speaker"]
        full = _unique(cands.get(i, []) + heuristic_candidates(b))
        s, e = b["start_time"], b["end_time"]
        n = max(1, math.ceil((e - s) / max_hold - 1e-6))
        n = min(n, max(1, int((e - s) / MIN_PART)))
        for k in range(n):
            ps, pe = s + (e - s) * k / n, s + (e - s) * (k + 1) / n
            choice = None
            for window in (4, 3, 2, 1, 0):
                banned = set(recent[spk][-window:]) if window else set()
                choice = next((p for p in full if p not in banned), None)
                if choice:
                    break
            recent[spk].append(choice)
            plan.append({"speaker": spk, "start_time": round(ps, 3), "end_time": round(pe, 3),
                         "png_id": choice, "text": b.get("text", "")[:60]})
    return plan


def choose(runner, beats, rep, stage="poses", cache_path=None, force=False, max_hold=3.2):
    """Plan final [{speaker, start_time, end_time, png_id}, ...] (mis en cache)."""
    beats = sorted(beats, key=lambda b: b["start_time"])
    key = hashlib.md5(json.dumps(beats, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if cache_path and not force and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("key") == key:
                rep.log(f"Poses : plan rechargé depuis le cache ({len(data['plan'])} shibis). "
                        "Coche « Choisir à nouveau les poses des shibis » pour le régénérer.", stage=stage)
                rep.stage_units(stage, 1)
                rep.stage_tick(stage)
                return data["plan"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    rep.log(f"Choix des poses pour {len(beats)} beats…", stage=stage)
    cands = _ask_candidates(runner, beats, rep, stage,
                            cache_file=(cache_path + ".lots") if cache_path else None, key=key, force=force)
    plan = _assign(beats, cands, max_hold)
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "plan": plan}, f, ensure_ascii=False, indent=1)
    used = len({p["png_id"] for p in plan})
    avg = sum(p["end_time"] - p["start_time"] for p in plan) / max(1, len(plan))
    rep.ok(f"Poses : {len(plan)} shibis, {used}/22 poses différentes, durée moyenne {avg:.1f} s.", stage=stage)
    return plan
