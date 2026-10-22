"""
speech.py — Savoir QUI parle, et transcrire.

Deux sources, comme la v1 :
1. Pistes audio séparées (recommandé, exact) : VAD sur la piste de chaque
   personne -> qui parle est un fait, pas une devinette. Gemini transcrit
   chaque piste séparément ; tout texte sans parole détectée est supprimé,
   toute parole non transcrite reçoit quand même un segment neutre.
2. Mode IA (une seule piste) : Gemini écoute le mix et devine le locuteur.

Nouveauté v2 en mode IA : on envoie l'AUDIO seul (les mêmes tranches mp3
légères que le dérush) au lieu de téléverser des tranches VIDÉO .mkv
entières — envoi ~100× plus léger, réponse bien plus rapide, même tâche
(transcrire des voix et reconnaître qui parle).
"""
import os
import json

from . import config, media, gemini as gm

SPEAKERS = config.SPEAKERS

def norm_speaker(s):
    s = str(s or "").lower()
    if "omg" in s:
        return "AgentOMG"
    if "marv" in s:
        return "Marvouch"
    return None

TRACK_PROMPT = """Cet audio est le micro d'UNE SEULE personne : __NAME__, un streamer Minecraft qui joue avec
__OTHER__. N'invente RIEN : s'il ne parle pas, ne renvoie rien. Ignore toute voix lointaine ou faible
d'une autre personne (écho du micro voisin) et les bruits de jeu.

Transcris SA parole en segments COURTS et PRÉCIS. Réponds en JSON pur (un tableau).
Règles :
- 1 segment = une phrase ou un groupe de mots dit d'une traite : 1 à 4 secondes idéalement, 6 maximum.
  Coupe à chaque phrase, question, exclamation, changement d'idée ou de ton, rire, cri.
- start_time / end_time = uniquement le moment où il PARLE (rires et cris compris). Aucun segment sur les silences.
- emotion : UNE valeur parmi : neutre, calme, joyeux, excité, amusé, surpris, choqué, peur, panique, colère,
  énervé, dégoûté, triste, déçu, gêné, curieux, réfléchi, confiant, sarcastique, fier, fatigué.
  Évite "neutre" sauf si la voix est vraiment plate : repère les rires, les cris, les moqueries, les doutes.
- intensity : 1 (calme), 2 (marqué), 3 (fort : cris, fous rires, grosse réaction).
- intent : UNE valeur parmi : explique, question, blague, réaction, insulte, ordre, doute, victoire, echec,
  danger, remerciement, salut, narration.
Si personne ne parle dans l'extrait : []

Format :
[{"start_time": 12.4, "end_time": 14.9, "text": "Texte dit", "emotion": "amusé", "intensity": 2, "intent": "blague"}]"""

AI_PROMPT = """
    Analyse cet extrait audio : enregistrement de deux streamers Minecraft qui discutent (AgentOMG et Marvouch).
    Transcris la parole en segments COURTS et PRÉCIS. Réponds en JSON pur (un tableau).

    Règles :
    - 1 segment = une phrase ou un groupe de mots dit d'une traite : 1 à 4 secondes idéalement, 6 maximum.
      Coupe à chaque phrase, question, exclamation, changement d'idée ou de ton, rire, cri.
    - start_time / end_time = uniquement le moment où la personne PARLE. Ne couvre jamais un silence ni un
      moment où personne ne parle : dans ce cas, n'émets aucun segment.
    - speaker : AgentOMG, Marvouch, both ou unknown.
    - ILS PEUVENT PARLER EN MÊME TEMPS (interruptions, phrases qui se chevauchent, rires ou cris simultanés) :
      dans ce cas émets DEUX segments qui se chevauchent dans le temps, un par personnage, chacun avec son
      propre texte. N'utilise "both" que s'ils disent ou crient exactement la même chose ensemble.
    - emotion : UNE valeur parmi : neutre, calme, joyeux, excité, amusé, surpris, choqué, peur, panique, colère,
      énervé, dégoûté, triste, déçu, gêné, curieux, réfléchi, confiant, sarcastique, fier, fatigué.
      Évite "neutre" sauf si la voix est vraiment plate : repère les rires, les cris, les moqueries, les doutes.
    - intensity : 1 (calme), 2 (marqué), 3 (fort : cris, fous rires, grosse réaction).
    - intent : UNE valeur parmi : explique, question, blague, réaction, insulte, ordre, doute, victoire, echec,
      danger, remerciement, salut, narration.

    Format :
    [
        {"start_time": 12.4, "end_time": 14.9, "speaker": "AgentOMG", "text": "Texte dit",
         "emotion": "amusé", "intensity": 2, "intent": "blague"}
    ]
    """


# ------------------------------------------------------------------ résolution des pistes
def resolve(info, track_agentomg, track_marvouch):
    """({personnage: index de flux 0-based}, message) ou (None, message)."""
    nums = {"AgentOMG": int(track_agentomg), "Marvouch": int(track_marvouch)}
    if min(nums.values()) < 1:
        return None, "les numéros de pistes commencent à 1"
    if nums["AgentOMG"] == nums["Marvouch"]:
        return None, "AgentOMG et Marvouch ont le même numéro de piste"
    n = len(info["audio_streams"])
    need = max(nums.values())
    if n < need:
        return None, f"{n} piste(s) audio seulement dans la vidéo (il en faut {need})"
    return ({k: v - 1 for k, v in nums.items()},
            f"AgentOMG = piste {nums['AgentOMG']}, Marvouch = piste {nums['Marvouch']} ({n} pistes audio)")


def speech_stats(speech, total, rep=None, stage=None):
    cov = {k: sum(e - s for s, e in v) for k, v in speech.items()}
    both = media.overlap_total(speech.get("AgentOMG", []), speech.get("Marvouch", []))
    stats = {"cov": cov, "both": both,
             "counts": {k: len(v) for k, v in speech.items()}}
    if rep and total:
        rep.log(f"AgentOMG parle {100 * cov['AgentOMG'] / total:.0f} % du temps "
                f"({len(speech['AgentOMG'])} interventions), Marvouch {100 * cov['Marvouch'] / total:.0f} % "
                f"({len(speech['Marvouch'])}), les deux en même temps {both:.0f} s.", stage=stage)
    return stats


# ------------------------------------------------------------------ recalage sur la parole réelle
def _minus(interval, covered):
    s, e = interval
    cur, out = s, []
    for a, b in covered:
        if b <= cur:
            continue
        if a >= e:
            break
        if a > cur:
            out.append((cur, min(a, e)))
        cur = max(cur, b)
        if cur >= e:
            break
    if cur < e:
        out.append((cur, e))
    return out


def align(speaker, speech, segs, pad=0.6, min_extra=0.4):
    """Recale les segments Gemini sur la parole détectée (logique v1 inchangée)."""
    out, prev_end = [], 0.0
    for seg in sorted(segs, key=lambda x: float(x.get("start_time", 0))):
        try:
            a, b = float(seg["start_time"]), float(seg["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= a:
            continue
        hits = [(s, e) for s, e in speech if e > a - pad and s < b + pad]
        if not hits:
            continue
        s0 = max(min(s for s, _ in hits), a - pad, prev_end)
        e0 = min(max(e for _, e in hits), b + pad)
        if e0 - s0 < 0.2:
            continue
        item = dict(seg)
        item.update({"start_time": round(s0, 3), "end_time": round(e0, 3),
                     "speaker": speaker, "exact": True})
        out.append(item)
        prev_end = e0
    covered = media.merge_ranges([(x["start_time"], x["end_time"]) for x in out])
    for iv in speech:
        for s, e in _minus(iv, covered):
            glued = False
            if e - s < 1.2:
                for x in out:
                    if abs(x["end_time"] - s) < 0.03:
                        x["end_time"], glued = round(e, 3), True
                        break
                    if abs(x["start_time"] - e) < 0.03:
                        x["start_time"], glued = round(s, 3), True
                        break
            if glued:
                continue
            if e - s >= min_extra:
                out.append({"start_time": round(s, 3), "end_time": round(e, 3), "speaker": speaker,
                            "text": "", "emotion": "neutre", "intensity": 1, "intent": "", "exact": True})
    return sorted(out, key=lambda x: x["start_time"])


# ------------------------------------------------------------------ transcription générique par tranches
def _transcribe_chunks(runner, jobs, cache, cache_file, rep, stage, total_units):
    """
    jobs : [(clé_cache, chunk, prompt, libellé)] — envoie chaque tranche à
    Gemini en parallèle, met en cache au fil de l'eau, fait avancer l'étape.
    """
    def work(job):
        _key, chunk, prompt, label = job
        tid = rep.task_start("gemini", label)
        try:
            f = runner.upload(chunk.path, tid, label)
            try:
                return runner.generate_json([f, prompt], "array", tid=tid, label=label, temperature=0.2)
            finally:
                runner.delete(f)
        finally:
            rep.task_end(tid)

    def done(job, data):
        key, _chunk, _prompt, label = job
        if data is None:
            rep.warn(f"{label} : pas de réponse exploitable (retentée au prochain lancement).", stage=stage)
        else:
            cache["chunks"][key] = data
            gm.save_chunk_cache(cache_file, cache)
            rep.log(f"{label} : {len(data)} segments.", stage=stage)
        rep.stage_tick(stage, label=f"{label} transcrite")

    return runner.run_parallel(jobs, work, on_done=done)


def analyze_tracks(runner, video, info, tracks_map, cache_dir, rep, stage="speech",
                   needed=None, cancel=None, rel_db=None, speech=None):
    """Segments {start_time, end_time, speaker, text, emotion, ...} en temps source.
    `speech` peut être fourni si la VAD a déjà tourné en tâche de fond."""
    if speech is None:
        speech = media.speech_intervals(video, tracks_map, cache_dir,
                                        rel_db=rel_db, cancel=cancel, rep=rep)
    speech_stats(speech, info["duration"], rep, stage)

    prompts = {spk: TRACK_PROMPT.replace("__NAME__", spk)
               .replace("__OTHER__", [o for o in SPEAKERS if o != spk][0]) for spk in SPEAKERS}
    cache_file = os.path.join(cache_dir, "tracks_chunks.json")
    cache = gm.load_chunk_cache(cache_file, gm.prompt_key("".join(prompts.values()), json.dumps(tracks_map, sort_keys=True)))

    jobs, offsets_by, total_units = [], {}, 0
    for spk in SPEAKERS:
        chunks = media.extract_audio_chunks(video, tracks_map[spk], cache_dir, cancel=cancel, rep=rep)
        offsets_by[spk] = chunks
        total_units += len(chunks)
        for ch in chunks:
            key = f"{spk}:{ch.index}"
            if ch.duration < media.MIN_CHUNK_S or key in cache["chunks"]:
                continue
            if needed is not None and media.overlap_total(needed, [(ch.offset, ch.offset + ch.duration)]) <= 0:
                continue
            if media.overlap_total(speech[spk], [(ch.offset, ch.offset + ch.duration)]) > 0:
                jobs.append((key, ch, prompts[spk], f"{spk} · tranche {ch.index + 1}/{len(chunks)}"))
            else:
                cache["chunks"][key] = []      # personne ne parle : rien à envoyer (économie)
    gm.save_chunk_cache(cache_file, cache)
    rep.stage_units(stage, total_units)
    for _ in range(total_units - len(jobs)):
        rep.stage_tick(stage)
    rep.log(f"{len(jobs)} tranche(s) à transcrire ({runner.workers} en parallèle) — "
            "les tranches muettes et déjà faites sont sautées.", stage=stage)

    err = _transcribe_chunks(runner, jobs, cache, cache_file, rep, stage, total_units)
    if err is not None and not any(cache["chunks"].values()):
        raise err

    result = []
    for spk in SPEAKERS:
        raw = []
        for ch in offsets_by[spk]:
            for seg in cache["chunks"].get(f"{spk}:{ch.index}", []):
                try:
                    seg = dict(seg)
                    seg["start_time"] = float(seg["start_time"]) + ch.offset
                    seg["end_time"] = float(seg["end_time"]) + ch.offset
                except (KeyError, TypeError, ValueError):
                    continue
                raw.append(seg)
        aligned = align(spk, speech[spk], raw)
        dropped = len(raw) - sum(1 for x in aligned if x.get("text"))
        rep.log(f"{spk} : {len(raw)} segments transcrits → {len(aligned)} après recalage "
                f"sur la parole réelle ({max(0, dropped)} sans parole détectée supprimés).", stage=stage)
        result += aligned
    result.sort(key=lambda x: x["start_time"])
    return result


def _nearest_speaker(gap_s, gap_e, segments):
    """Pour un trou sans locuteur détecté en mode IA : le locuteur le plus proche dans le temps."""
    best, best_d = None, float("inf")
    for seg in segments:
        spk = norm_speaker(seg.get("speaker"))
        if not spk:
            continue
        try:
            s, e = float(seg["start_time"]), float(seg["end_time"])
        except (TypeError, ValueError):
            continue
        d = min(abs(gap_s - e), abs(gap_e - s), abs((s+e)/2 - (gap_s+gap_e)/2))
        if d < best_d:
            best, best_d = spk, d
    return best or "AgentOMG"


def analyze_ai(runner, video, info, chunks_mix, cache_dir, rep, stage="speech", needed=None):
    """Mode secours : Gemini écoute le mix et devine qui parle (audio seul)."""
    cache_file = os.path.join(cache_dir, "analysis_chunks.json")
    cache = gm.load_chunk_cache(cache_file, gm.prompt_key(AI_PROMPT))
    jobs = []
    for ch in chunks_mix:
        if ch.duration < media.MIN_CHUNK_S or str(ch.index) in cache["chunks"]:
            continue
        if needed is not None and media.overlap_total(needed, [(ch.offset, ch.offset + ch.duration)]) <= 0:
            continue
        jobs.append((str(ch.index), ch, AI_PROMPT, f"Mix · tranche {ch.index + 1}/{len(chunks_mix)}"))
    rep.stage_units(stage, len(chunks_mix))
    for _ in range(len(chunks_mix) - len(jobs)):
        rep.stage_tick(stage)
    rep.log(f"{len(chunks_mix)} tranches : {len(jobs)} à analyser ({runner.workers} en parallèle).", stage=stage)

    err = _transcribe_chunks(runner, jobs, cache, cache_file, rep, stage, len(chunks_mix))
    if err is not None and not cache["chunks"]:
        raise err

    all_segments = []
    by_index = {c.index: c for c in chunks_mix}
    for i_str, segs in cache["chunks"].items():
        ch = by_index.get(int(i_str))
        if ch is None:
            continue
        for seg in segs or []:
            try:
                seg = dict(seg)
                seg["start_time"] = round(float(seg["start_time"]) + ch.offset, 2)
                seg["end_time"] = round(float(seg["end_time"]) + ch.offset, 2)
            except (KeyError, TypeError, ValueError):
                continue
            all_segments.append(seg)
    all_segments.sort(key=lambda x: x["start_time"])

    # --- filet de sécurité v2.1 : si Gemini a oublié une réplique mais qu'on
    # entend bien quelqu'un parler sur le mix, on pose quand même un shibi neutre
    # (au lieu de laisser 20 s de vide). Évite les trous observés en mode IA.
    try:
        speech_mix, _info = media.detect_speech(video, 0, media.vad_params(), cancel=None)
        if speech_mix:
            if needed is not None:
                # on ne comble que dans les zones réellement utilisées du projet
                speech_mix = [iv for iv in speech_mix if media.overlap_total([iv], needed) > 0.05]
            covered = media.merge_ranges([
                (float(s["start_time"]), float(s["end_time"]))
                for s in all_segments if norm_speaker(s.get("speaker")) and s.get("start_time") is not None
            ])
            filled = 0
            for iv in speech_mix:
                for s, e in _minus(iv, covered):
                    if e - s < 0.4:
                        continue
                    spk = _nearest_speaker(s, e, all_segments)
                    all_segments.append({
                        "start_time": round(s, 3), "end_time": round(e, 3),
                        "speaker": spk, "text": "", "emotion": "neutre",
                        "intensity": 1, "intent": "", "exact": True, "fallback": True,
                    })
                    filled += 1
                    covered = media.merge_ranges(covered + [(s, e)])
            if filled:
                all_segments.sort(key=lambda x: x["start_time"])
                rep.log(f"Mode IA : {filled} trou(s) sans shibi comblé(s) par la détection de voix (évite les passages muets).", stage=stage)
    except Exception as e:
        rep.warn(f"Filet anti-trou IA ignoré ({e}).", stage=stage)

    missing = [c.index + 1 for c in chunks_mix
               if c.duration >= media.MIN_CHUNK_S and str(c.index) not in cache["chunks"]
               and (needed is None or media.overlap_total(needed, [(c.offset, c.offset + c.duration)]) > 0)]
    if missing:
        rep.warn(f"Analyse incomplète : tranche(s) {missing} sans résultat. "
                 "Relance : seules celles-ci seront refaites.", stage=stage)
    rep.ok(f"Analyse terminée : {len(all_segments)} segments.", stage=stage)
    return all_segments
