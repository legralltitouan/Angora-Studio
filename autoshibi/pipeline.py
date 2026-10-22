"""
pipeline.py — L'orchestrateur : un job (réglages) -> un montage CapCut.

Deux modes, comme la v1 :
  generate : dérush + zooms + shibis depuis la vidéo brute, nouveau projet
             cloné du modèle ;
  edit     : zooms + shibis ajoutés à un projet déjà monté à la main
             (sur place avec sauvegarde, ou sur une copie).

Orchestration v2 : les travaux ffmpeg (silences, extraction, VAD) tournent
EN PARALLÈLE des appels Gemini du dérush ; chaque tranche terminée est mise
en cache immédiatement ; la progression affichée compte les unités réelles.
"""
import os
import re
import glob
import json
import time
import threading
import subprocess
import concurrent.futures as cf

from . import verification, config, media, gemini as gm, derush, speech, poses, capcut, projedit, checks
from .events import Reporter, CancelToken, Cancelled, Heartbeat


class Fail(Exception):
    """Erreur attendue : message montré tel quel à l'utilisateur."""


# ------------------------------------------------------------------ divers
def close_capcut(rep=None):
    killed = 0
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and "capcut" in proc.info["name"].lower():
                    proc.kill()
                    killed += 1
            except Exception:
                pass
    except ImportError:
        if rep:
            rep.warn("psutil absent : impossible de fermer CapCut automatiquement.")
    if killed:
        time.sleep(2)
        if rep:
            rep.log(f"CapCut fermé ({killed} processus).")


def capcut_running():
    try:
        import psutil
        return any("capcut" in (p.info.get("name") or "").lower()
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def launch_capcut():
    """Tente d'ouvrir CapCut (Windows/macOS). Retourne True si lancé."""
    try:
        if os.name == "nt":
            patterns = [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "CapCut", "Apps", "*", "CapCut.exe"),
                os.path.join(os.environ.get("PROGRAMFILES", ""), "CapCut", "CapCut.exe"),
            ]
            for pat in patterns:
                hits = sorted(glob.glob(pat), reverse=True)
                if hits:
                    subprocess.Popen([hits[0]])
                    return True
            os.startfile("CapCut")            # dernier espoir : app enregistrée
            return True
        subprocess.Popen(["open", "-a", "CapCut"])   # macOS
        return True
    except Exception:
        return False


def resolve_video(job):
    v = (job.get("video") or "").strip()
    if not v:
        v = config.read_env_key("VIDEO_FILE") or os.getenv("VIDEO_FILE") or "video.mkv"
    if os.path.isabs(v):
        return v
    for base in (os.getcwd(), config.BASE_DIR):
        cand = os.path.join(base, v)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return os.path.join(config.BASE_DIR, v)


def style_overrides(job):
    return {"derush_level": int(job["derush_level"]), "max_pause_s": float(job["max_pause_s"]),
            "max_shot_s": float(job["max_shot_s"]), "zoom_coverage": float(job["zoom_coverage"]),
            "zoom_intensity": float(job["zoom_intensity"]), "zoom_out_ratio": float(job["zoom_out_ratio"])}


def clear_video_cache(cache_dir, rep=None):
    names = ["derush_chunks.json", "analysis_chunks.json", "tracks_chunks.json", "vad.json",
             "silences.json", "derush_report.json", "analysis_report.json",
             "analysis_tracks_report.json", "poses_report.json", "poses_report.json.lots"]
    names += [os.path.basename(p) for p in glob.glob(os.path.join(cache_dir, "poses_edit_*.json*"))]
    for name in set(names):
        try:
            os.remove(os.path.join(cache_dir, name))
        except OSError:
            pass
    if rep:
        rep.log("Cache de cette vidéo effacé (les extraits audio déjà préparés sont conservés).")


def stages_for(job):
    want_shibi = job["shibi"] and job["shibi_source"] != "off"
    stages = [("prep", "Préparation", 6)]
    if job["mode"] == "generate":
        stages.append(("derush", "Dérush" if job["derush"] else "Découpage des plans", 30 if job["derush"] else 6))
    if job["mode"] == "generate" and job["derush"] and job.get("manual_verify"):
        stages.append(("verify", "Vérifier les coupes", 2))
    if want_shibi:
        stages += [("speech", "Qui parle ?", 36), ("poses", "Choix des poses", 14)]
    stages.append(("capcut", "Projet CapCut", 14))
    return stages


def _runner(rep, cancel, workers):
    client = gm.make_client(timeout_s=300)
    return gm.Runner(client, config.gemini_models(), rep, cancel, workers=workers)


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ point d'entrée
def run_job(job, rep, cancel):
    """Exécute un job complet. Émet les événements dans `rep` ; retourne le résumé."""
    job = config.coerce_job(job)
    hb = Heartbeat(rep).start()
    summary = {}
    try:
        if job["mode"] == "edit":
            summary = _run_edit(job, rep, cancel)
        else:
            summary = _run_generate(job, rep, cancel)
        rep.finish("ok", summary=summary)
        return summary
    except Fail as e:
        rep.error(str(e))
        rep.finish("error", error=str(e))
        raise
    except Cancelled:
        rep.warn("Arrêté par l'utilisateur. Tout ce qui était déjà analysé est en cache : "
                 "relancer reprendra là où ça s'est arrêté.")
        rep.finish("cancelled", error="Arrêté par l'utilisateur.")
        raise
    except Exception as e:
        chain = _error_chain(e)
        msg = f"Erreur inattendue : {type(e).__name__}: {e}"
        if any(k in chain for k in ("CERTIFICATE_VERIFY_FAILED", "ConnectError",
                                    "ConnectTimeout", "getaddrinfo failed")):
            msg = ("Connexion à Gemini impossible (réseau / certificat, pas un bug du montage). "
                   "Lance « Vérifier » pour voir ce que ton réseau renvoie.")
        import traceback
        detail = traceback.format_exc()
        rep.error(msg)
        rep.finish("error", error=msg, detail=detail)
        raise Fail(msg)
    finally:
        hb.stop()


def _error_chain(e):
    texts = []
    while e is not None:
        texts.append(f"{type(e).__name__}: {e}")
        e = e.__cause__ or e.__context__
    return " | ".join(texts)


# ------------------------------------------------------------------ parallélisation de préparation
class _Prefetch:
    """Lance des travaux médias en tâche de fond pendant que Gemini travaille."""

    def __init__(self):
        self.ex = cf.ThreadPoolExecutor(max_workers=3)
        self.futs = {}

    def submit(self, name, fn):
        self.futs[name] = self.ex.submit(fn)

    def result(self, name):
        return self.futs[name].result()

    def close(self):
        self.ex.shutdown(wait=False)


# ------------------------------------------------------------------ mode génération
def _run_generate(job, rep, cancel):
    want_shibi = job["shibi"] and job["shibi_source"] != "off"
    need_gemini = job["derush"] or want_shibi
    video = resolve_video(job)

    # -------- préparation
    rep.stage_start("prep", label="Vérification de l'installation")
    items = checks.check_generate(job, video, full=(job["action"] == "check"))
    checks.render(items, rep, "prep")
    if job["action"] == "check":
        ok = not checks.has_fail(items)
        rep.stage_done("prep", "Vérification terminée")
        if not ok:
            raise Fail("Corrige les points marqués ❌ puis relance la vérification.")
        return {"checked": True, "ok": True, "mode": "generate"}
    if checks.has_fail(items):
        raise Fail("Corrige les points marqués ❌ puis relance.")

    info = media.probe(video)
    cache_dir = media.cache_dir_for(video)
    if job["fresh"]:
        clear_video_cache(cache_dir, rep)
    if job["close_capcut"]:
        close_capcut(rep)
    runner = _runner(rep, cancel, job["workers"]) if need_gemini else None

    # style : profil YouTube (une fois) puis les réglages de l'utilisateur gagnent
    style = derush.load_style()
    if job["derush"] and runner and config.STYLE_URLS and not os.path.exists(config.STYLE_PATH):
        rep.log("Premier lancement : analyse des vidéos de référence pour le rythme de coupe…", stage="prep")
        try:
            derush.build_style_profile(runner, config.STYLE_URLS, config.STYLE_PATH, rep)
            style = derush.load_style()
        except Exception as e:
            rep.warn(f"Profil de style ignoré ({e}) : paramètres par défaut.")
    style.update(style_overrides(job))

    # ffmpeg en parallèle : silences + extraction du mix
    pre = _Prefetch()
    tid = rep.task_start("ffmpeg", "Silences + extraction audio")
    pre.submit("silences", lambda: media.detect_silences(video, info["duration"], cache_dir,
                                                         cancel=cancel, rep=rep))
    pre.submit("mix", lambda: media.extract_audio_chunks(video, 0, cache_dir, cancel=cancel, rep=rep))
    silences = pre.result("silences")
    chunks_mix = pre.result("mix")
    rep.task_end(tid, detail=f"{len(chunks_mix)} tranches audio prêtes")

    # qui parle : la VAD et l'extraction des pistes démarrent tout de suite,
    # pendant que le dérush occupe Gemini (réseau) de son côté.
    tracks_map, speech_pre = None, {}
    if want_shibi and job["shibi_source"] == "tracks":
        tracks_map, msg = speech.resolve(info, job["track_agentomg"], job["track_marvouch"])
        if tracks_map:
            rep.log(f"Pistes audio séparées : {msg}", stage="prep")

            def _vad_and_extract():
                sp = media.speech_intervals(video, tracks_map, cache_dir,
                                            rel_db=job["vad_rel_db"], cancel=cancel, rep=rep)
                for aidx in tracks_map.values():
                    media.extract_audio_chunks(video, aidx, cache_dir, cancel=cancel, rep=rep)
                return sp
            pre.submit("vad", _vad_and_extract)
        else:
            rep.warn(f"Pistes séparées indisponibles ({msg}) : Gemini devra deviner qui parle.", stage="prep")
    rep.stage_done("prep", "Prêt")

    # -------- dérush
    rep.stage_start("derush", total=len(chunks_mix),
                    label="Analyse des tranches par Gemini" if job["derush"] else "Plans pour les zooms")
    if job["derush"]:
        keep = derush.run_derush(runner, video, chunks_mix, silences, style, cache_dir, rep, "derush")
    else:
        keep = derush.run_derush(runner, video, chunks_mix, silences, style, cache_dir, rep, "derush", cut=False)
    _save_json(os.path.join(cache_dir, "derush_report.json"),
               [[round(a, 3), round(b, 3)] for a, b in keep])
    kept_s = sum(b - a for a, b in keep)
    removed_s = info["duration"] - kept_s
    rep.stage_done("derush", f"{len(keep)} plans · {kept_s / 60:.1f} min conservées ({removed_s/60:.1f} min coupées)")

    if job["derush"] and job.get("manual_verify"):
        keep = verification.request(keep, silences, video, info["duration"],
                                    chunks_mix, cache_dir, rep, cancel)
        # Rebuild shot divisions for zoom planning after the human edits.
        keep = derush._split_long(keep, silences, job["max_shot_s"])
        kept_s = sum(b - a for a, b in keep)
        _save_json(os.path.join(cache_dir, "derush_report.json"), keep)
        rep.log(f"Après vérification : {kept_s/60:.1f} min conservées, {len(keep)} plans.")

    # -------- qui parle + poses
    by_speaker, n_segments = None, 0
    style_shibi = capcut.ShibiStyle(scale=job["shibi_scale"], swap_sides=job["swap_sides"],
                                    linger_s=job["linger_s"])
    if want_shibi:
        rep.stage_start("speech", label="Transcription")
        if tracks_map:
            speech_pre = pre.result("vad")
            segments = speech.analyze_tracks(runner, video, info, tracks_map, cache_dir, rep,
                                             "speech", needed=keep, cancel=cancel, rel_db=job["vad_rel_db"],
                                             speech=speech_pre)
            report_name, exact = "analysis_tracks_report.json", True
        else:
            segments = speech.analyze_ai(runner, video, info, chunks_mix, cache_dir, rep, "speech", needed=keep)
            report_name, exact = "analysis_report.json", False
        if not segments:
            raise Fail("Aucune réplique détectée dans la vidéo : regarde le journal "
                       "(erreurs Gemini éventuelles) puis relance — le cache reprendra le reste.")
        _save_json(os.path.join(cache_dir, report_name), segments)
        n_segments = len(segments)
        rep.stage_done("speech", f"{n_segments} répliques")

        rep.stage_start("poses", label="Choix des poses par réplique")
        beats = derush.remap_segments(poses.refine_segments(segments, rep), keep, rep=rep)
        plan = poses.choose(runner, beats, rep, "poses",
                            cache_path=os.path.join(cache_dir, "poses_report.json"),
                            force=job["repick"], max_hold=job["max_hold"])
        plan = poses.add_linger(plan, style_shibi.linger_s)
        by_speaker = capcut.prepare_shibi_segments(plan, rep)
        rep.log(f"Shibis : AgentOMG {len(by_speaker['AgentOMG'])} · Marvouch {len(by_speaker['Marvouch'])}",
                stage="poses")
        if sum(len(v) for v in by_speaker.values()) == 0:
            raise Fail("Aucun shibi exploitable (images manquantes ? erreurs Gemini ?).")
        rep.stage_done("poses", "Poses choisies")
    pre.close()

    # -------- projet CapCut
    rep.stage_start("capcut", label="Clonage du projet modèle")
    pdir = config.find_projects_dir(job.get("projects_dir") or None)
    template = job.get("template") or "AutoShibi-V4"
    stem = re.sub(r"[^\w-]+", "_", os.path.splitext(os.path.basename(video))[0])[:30]
    new_name = f"{template}-Gen-{stem}-{time.strftime('%Y%m%d-%H%M%S')}"
    new_dir = os.path.join(pdir, new_name)
    ref_dir = os.path.join(pdir, template)
    p_id, t_id = capcut.clone_project(ref_dir, new_dir, new_name, rep)

    files = capcut.draft_files(new_dir)
    if not files:
        raise Fail(f"Aucun draft_content.json dans le projet cloné ({new_dir}).")
    ok, old_paths, counts = 0, set(), {}
    rep.stage_units("capcut", len(files))
    for path in files:
        rel = os.path.relpath(path, new_dir)
        try:
            draft = capcut.load_draft(path)
            main_base, _mode = derush.apply_keep_to_draft(draft, keep, style,
                                                          zoom=job["zoom"], seed=42, rep=rep)
            old_paths.add(capcut.retarget_main_video(draft, main_base, video, info, rep))
            counts = (capcut.build_shibi_tracks_generate(draft, style_shibi, by_speaker, main_base)
                      if by_speaker else {})
            capcut.save_draft(path, draft)
            ok += 1
            rep.log(f"{rel} : timeline écrite" + (f", shibis {counts}" if counts else ""), stage="capcut")
        except Cancelled:
            raise
        except Exception as e:
            rep.warn(f"Fichier ignoré : {rel} ({type(e).__name__}: {e})", stage="capcut")
        rep.stage_tick("capcut", label=f"Timeline {ok}/{len(files)}")
    if ok == 0:
        raise Fail("Aucun draft_content.json n'a pu être généré.")
    for old in old_paths:
        capcut.update_meta_video_path(new_dir, old, video.replace("\\", "/"))
    capcut.register_in_registry(pdir, ref_dir, new_dir, new_name, p_id, t_id, rep)
    rep.stage_done("capcut", "Projet installé dans CapCut")
    if job.get("open_capcut"):
        launch_capcut()
    rep.ok(f"✅ Projet « {new_name} » prêt : ouvre CapCut et sélectionne-le.")
    return {"mode": "generate", "project": new_name, "project_dir": new_dir,
            "duration_before_s": round(info["duration"], 1), "duration_after_s": round(kept_s, 1),
            "plans": len(keep), "segments": n_segments, "shibis": counts,
            "gemini": dict(runner.stats) if runner else {}}


# ------------------------------------------------------------------ mode édition
def _run_edit(job, rep, cancel):
    want_zoom = bool(job["zoom"])
    want_shibi = bool(job["shibi"]) and job["shibi_source"] != "off"
    if not (want_zoom or want_shibi):
        raise Fail("Rien à faire : active les zooms et/ou les shibis.")
    if not (job.get("project") or "").strip():
        raise Fail("Choisis le projet CapCut à modifier.")

    rep.stage_start("prep", label="Vérification du projet")
    items = checks.check_edit(job, full=(job["action"] == "check"))
    checks.render(items, rep, "prep")
    if job["action"] == "check":
        ok = not checks.has_fail(items)
        rep.stage_done("prep", "Vérification terminée")
        if not ok:
            raise Fail("Corrige les points marqués ❌ puis relance la vérification.")
        return {"checked": True, "ok": True, "mode": "edit"}
    if checks.has_fail(items):
        raise Fail("Corrige les points marqués ❌ puis relance.")

    pdir = config.find_projects_dir(job.get("projects_dir") or None)
    project_dir = os.path.join(pdir, job["project"])
    if job["close_capcut"]:
        close_capcut(rep)

    target_dir, reg = project_dir, None
    if job["work_on_copy"]:
        rep.log("Copie du projet…", stage="prep")
        new_name = f"{job['project']}-Angora-{time.strftime('%Y%m%d-%H%M%S')}"
        target_dir = os.path.join(pdir, new_name)
        reg = capcut.clone_project(project_dir, target_dir, new_name, rep) + (new_name,)
        rep.log(f"Travail sur la copie : {new_name}", stage="prep")

    drafts = {}
    for path in capcut.draft_files(target_dir):
        try:
            drafts[path] = capcut.load_draft(path)
            projedit.main_track(drafts[path])
        except (ValueError, RuntimeError) as e:
            rep.warn(f"Fichier ignoré : {os.path.relpath(path, target_dir)} ({e})", stage="prep")
    if not drafts:
        raise Fail("Aucun draft_content.json exploitable dans ce projet.")

    style = derush.load_style()
    style.update(style_overrides(job))
    sample = checks.sample_from_template(job, pdir) if want_zoom else None
    style_shibi = capcut.ShibiStyle(scale=job["shibi_scale"], swap_sides=job["swap_sides"],
                                    linger_s=job["linger_s"])

    # -------- qui parle + poses (une seule fois pour la vidéo source)
    plan_cache, video = {}, None
    if want_shibi:
        runner = _runner(rep, cancel, job["workers"])
        all_clips = {p: projedit.main_clips(d) for p, d in drafts.items()}
        first = next(iter(drafts))
        video = (job.get("video") or "").strip() or projedit.dominant_path(all_clips[first])
        video = os.path.normpath(video) if video else video
        if not video or not os.path.exists(video):
            raise Fail(f"Vidéo source introuvable : {video or '(aucune)'}. "
                       "Choisis-la dans « Vidéo source ».")
        info = media.probe(video)
        cache_dir = media.cache_dir_for(video)
        if job["fresh"]:
            clear_video_cache(cache_dir, rep)
        needed = media.merge_ranges([r for clips in all_clips.values()
                                      for r in projedit.needed_ranges(
                                          [c for c in clips if os.path.normpath(c["path"]) == video] or clips)])
        used = sum(b - a for a, b in needed)
        rep.log(f"Vidéo source : {os.path.basename(video)} — {used / 60:.1f} min réellement "
                "utilisées dans le projet (seules elles sont transcrites).", stage="prep")
        rep.stage_done("prep", "Projet lu")

        rep.stage_start("speech", label="Transcription des zones utilisées")
        tracks_map = None
        if job["shibi_source"] == "tracks":
            tracks_map, msg = speech.resolve(info, job["track_agentomg"], job["track_marvouch"])
            if tracks_map:
                rep.log(f"Pistes audio séparées : {msg}", stage="speech")
            else:
                rep.warn(f"Pistes séparées indisponibles ({msg}) : Gemini devra deviner.", stage="speech")
        if tracks_map:
            segments = speech.analyze_tracks(runner, video, info, tracks_map, cache_dir, rep,
                                             "speech", needed=needed, cancel=cancel,
                                             rel_db=job["vad_rel_db"])
            exact = True
        else:
            chunks_mix = media.extract_audio_chunks(video, 0, cache_dir, cancel=cancel, rep=rep)
            segments = speech.analyze_ai(runner, video, info, chunks_mix, cache_dir, rep,
                                         "speech", needed=needed)
            exact = False
        if not segments:
            raise Fail("Aucune réplique détectée dans les zones utilisées de la vidéo.")
        beats = poses.refine_segments(segments, rep)
        rep.stage_done("speech", f"{len(segments)} répliques")

        rep.stage_start("poses", label="Choix des poses par timeline")
        sigs = {}
        for p, d in drafts.items():
            clips = projedit.main_clips(d)
            sigs.setdefault(projedit.signature(clips), clips)
        for sig, clips in sigs.items():
            on_tl = projedit.remap_beats(beats, [c for c in clips
                                                 if os.path.normpath(c["path"]) == video] or clips, rep=rep)
            plan = poses.choose(runner, on_tl, rep, "poses",
                                cache_path=os.path.join(cache_dir, f"poses_edit_{sig}.json"),
                                force=job["repick"], max_hold=job["max_hold"])
            plan = poses.add_linger(plan, style_shibi.linger_s)
            plan_cache[sig] = capcut.prepare_shibi_segments(plan, rep)
        rep.stage_done("poses", "Poses choisies")
    else:
        rep.stage_done("prep", "Projet lu")

    # -------- écriture (sauvegarde d'abord !)
    rep.stage_start("capcut", label="Sauvegarde puis modification")
    backup = projedit.backup_files(target_dir, list(drafts))
    rep.log(f"Sauvegarde de l'état actuel : {backup}", stage="capcut")
    rep.stage_units("capcut", len(drafts))
    totals = {"zoom": 0, "dezoom": 0, "shibis": {}}
    for n, (path, draft) in enumerate(drafts.items(), start=1):
        cancel.check()
        main = projedit.main_track(draft)
        clips = projedit.main_clips(draft, main)
        rep.log(f"{os.path.relpath(path, target_dir)} : {len(clips)} plans, "
                f"{max((c['tl_b'] for c in clips), default=0):.0f} s", stage="capcut")
        if want_zoom:
            st = projedit.apply_zoom(draft, style, keep_existing=job["keep_existing_zoom"], sample=sample)
            totals["zoom"] += st["zoom"]
            totals["dezoom"] += st["dezoom"]
            extra = []
            if st["already"]:
                extra.append(f"{st['already']} déjà animés conservés")
            if st["speed"]:
                extra.append(f"{st['speed']} à vitesse modifiée ignorés")
            if st["short"]:
                extra.append(f"{st['short']} trop courts")
            if st["untouched"]:
                extra.append(f"{st['untouched']} laissés fixes (fréquence {int(style['zoom_coverage'] * 100)} %)")
            rep.log(f"Zooms : {st['zoom']} zooms + {st['dezoom']} dézooms sur {st['clips']} plans "
                    f"(keyframes : {st['format']})" + ((" — " + ", ".join(extra)) if extra else ""), stage="capcut")
            if st["format"].startswith("format générique"):
                rep.warn("Aucun keyframe d'exemple : si les zooms ne bougent pas dans CapCut, ajoute "
                         "à la main un keyframe Échelle sur un plan et relance.", stage="capcut")
        if want_shibi:
            old = projedit.previous_shibi_tracks(draft, main)
            if old and job["replace_old_shibis"]:
                projedit.remove_tracks(draft, old)
                rep.log(f"{len(old)} ancienne(s) piste(s) de shibis remplacée(s).", stage="capcut")
            by_speaker = plan_cache[projedit.signature(clips)]
            if sum(len(v) for v in by_speaker.values()) == 0:
                rep.warn("Aucun shibi à poser sur cette timeline.", stage="capcut")
            else:
                counts = projedit.add_shibi_tracks(draft, style_shibi, by_speaker, main)
                totals["shibis"] = counts
                rep.log(f"Shibis posés : {counts} (2 nouvelles pistes au-dessus des tiennes).", stage="capcut")
        capcut.save_draft(path, draft)
        rep.stage_tick("capcut", label=f"Timeline {n}/{len(drafts)} écrite")

    if reg:
        p_id, t_id, new_name = reg
        capcut.register_in_registry(pdir, project_dir, target_dir, new_name, p_id, t_id, rep)
    rep.stage_done("capcut", "Projet modifié")
    if job.get("open_capcut"):
        launch_capcut()
    rep.ok(f"✅ Projet « {os.path.basename(target_dir)} » modifié. Ouvre CapCut et rouvre le projet.")
    rep.log(f"Pour revenir en arrière : ferme CapCut et recopie les fichiers de {backup} à leur place.")
    return {"mode": "edit", "project": os.path.basename(target_dir), "project_dir": target_dir,
            "backup": backup, "zooms": totals["zoom"], "dezooms": totals["dezoom"],
            "shibis": totals["shibis"]}
