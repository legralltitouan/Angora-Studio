"""
demo.py — Mode démo (aperçu de l'interface sans CapCut ni clé Gemini).

Activé uniquement avec --demo (ou AUTOSHIBI_DEMO=1) : projets factices et
montage simulé qui émet EXACTEMENT les mêmes événements que le vrai moteur,
pour montrer l'interface animée. Aucun fichier n'est écrit.
"""
import time
import random
import threading

from .events import Cancelled

PROJECTS = [
    {"name": "Minecraft EP12 - derive finale", "modified": 1761122400,
     "info": {"clips": 148, "duration_s": 1320.0, "video_path": "D:/rec/REC 2026-10-21 Minecraft.mkv",
              "other_videos": 0, "speed_changed": 2, "with_scale_keyframes": 0,
              "previous_shibi_tracks": 0, "has_keyframe_sample": True, "canvas": (3840, 2160)}},
    {"name": "AutoShibi-V4", "modified": 1760600000,
     "info": {"clips": 1, "duration_s": 600.0, "video_path": "D:/rec/modele.mp4",
              "other_videos": 0, "speed_changed": 0, "with_scale_keyframes": 1,
              "previous_shibi_tracks": 0, "has_keyframe_sample": True, "canvas": (3840, 2160)}},
    {"name": "Best-of rires - montage main", "modified": 1760500000,
     "info": {"clips": 86, "duration_s": 540.0, "video_path": "D:/rec/bestof.mkv",
              "other_videos": 1, "speed_changed": 0, "with_scale_keyframes": 12,
              "previous_shibi_tracks": 2, "has_keyframe_sample": True, "canvas": (1920, 1080)}},
]

VIDEO_INFO = {"path": "REC 2026-10-22 - session Minecraft.mkv", "width": 3840, "height": 2160,
              "duration": 2820.0,
              "audio_streams": [
                  {"n": 1, "aidx": 0, "channels": 2, "codec": "aac", "title": "Mix"},
                  {"n": 2, "aidx": 1, "channels": 1, "codec": "aac", "title": "Micro AgentOMG"},
                  {"n": 3, "aidx": 2, "channels": 1, "codec": "aac", "title": "Discord Marvouch"}],
              "size": 4_800_000_000}

TRACK_LEVELS = [
    {"n": 1, "aidx": 0, "mean_db": -22.4, "peak_db": -6.0, "voice": 0.62, "silent": False},
    {"n": 2, "aidx": 1, "mean_db": -31.0, "peak_db": -9.5, "voice": 0.41, "silent": False},
    {"n": 3, "aidx": 2, "mean_db": -33.8, "peak_db": -11.2, "voice": 0.37, "silent": False},
]


def preflight(job):
    def it(s, label, hint=""):
        return {"status": s, "label": label, "hint": hint}
    if job.get("mode") == "edit":
        nom = job.get("project") or PROJECTS[0]["name"]
        return [it("ok", "Projets CapCut : C:/Users/Demo/AppData/Local/CapCut/…/com.lveditor.draft"),
                it("ok", f"Projet « {nom} » : 148 plans, 22.0 min, canvas 3840x2160"),
                it("ok", "Exemple de keyframe d'échelle trouvé : format de TA version de CapCut"),
                it("ok", "ffmpeg installé"), it("ok", "Clé Gemini trouvée"),
                it("ok", "Poses AgentOMG : 22 images"), it("ok", "Poses Marvouch : 22 images"),
                it("ok", "Pistes audio séparées : AgentOMG = piste 2, Marvouch = piste 3 → qui parle est exact")]
    return [it("ok", "ffmpeg installé"), it("ok", "ffprobe installé"), it("ok", "Clé Gemini trouvée"),
            it("ok", "Vidéo : REC 2026-10-22 - session Minecraft.mkv (3840x2160, 47.0 min, 3 piste(s) audio)"),
            it("ok", "Pistes audio séparées : AgentOMG = piste 2, Marvouch = piste 3 → qui parle est exact"),
            it("ok", "Poses AgentOMG : 22 images"), it("ok", "Poses Marvouch : 22 images"),
            it("ok", "Projet modèle : AutoShibi-V4"),
            it("ok", "Exemple de keyframe d'échelle trouvé : le format de zoom de TA version de CapCut sera copié"),
            it("warn", "Mode démo", "Aucun vrai fichier n'est lu ni écrit")]


def estimate(job):
    return {"duration_s": 2820, "chunks": 10, "derush_calls": 7, "derush_cached": 3,
            "speech_calls": 14, "speech_cached": 6, "note": "Estimation démo"}


def fake_asset_svg(who, n):
    if who == "Marvouch":
        body, dark = "#5B8DEF", "#2F5FB3"
    else:
        body, dark = "#4CB782", "#2E7D4F"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240">
<rect width="240" height="240" rx="18" fill="#F5F4ED"/>
<circle cx="120" cy="86" r="42" fill="{body}"/>
<path d="M62 210c6-52 30-78 58-78s52 26 58 78z" fill="{dark}"/>
<circle cx="104" cy="80" r="6" fill="#fff"/><circle cx="136" cy="80" r="6" fill="#fff"/>
<path d="M104 102q16 12 32 0" stroke="#fff" stroke-width="6" fill="none" stroke-linecap="round"/>
<text x="120" y="232" text-anchor="middle" font-family="Segoe UI, sans-serif" font-size="17" fill="#6B6A63">pose {n}</text>
</svg>'''.encode("utf-8")


def _sleep(cancel, secs):
    end = time.time() + secs
    while time.time() < end:
        cancel.check()
        time.sleep(min(0.15, max(0.0, end - time.time())))


def _gemini_tasks(rep, cancel, stage, total, label_fmt, per_unit_logs, already=0):
    """Simule des appels Gemini parallèles visibles dans l'interface."""
    rep.stage_units(stage, total + already)
    for _ in range(already):
        rep.stage_tick(stage)
    lock = threading.Lock()
    done = {"n": 0}

    def one(i):
        tid = rep.task_start("gemini", label_fmt.format(i=i + 1, n=total))
        try:
            rep.task_update(tid, label_fmt.format(i=i + 1, n=total) + " — envoi à Google…")
            _sleep(cancel, random.uniform(0.7, 1.5))
            rep.task_update(tid, label_fmt.format(i=i + 1, n=total) + " — gemini-3.8-flash réfléchit…")
            _sleep(cancel, random.uniform(0.5, 1.2))
        finally:
            rep.task_end(tid)
        with lock:
            done["n"] += 1
            rep.log(per_unit_logs(i + 1, total), stage=stage)
            rep.stage_tick(stage, label=f"{already + done['n']}/{total + already} traitées")

    threads = []
    i = 0
    while i < total:
        batch = [threading.Thread(target=one, args=(j,), daemon=True)
                 for j in range(i, min(i + 3, total))]
        for t in batch:
            t.start()
        for t in batch:
            t.join()
        i += 3
        cancel.check()


def simulate(job, rep, cancel):
    from .events import Heartbeat
    hb = Heartbeat(rep).start()
    try:
        mode = job.get("mode", "edit")
        rep.stage_start("prep", label="Vérification de l'installation")
        for it in preflight(job):
            rep.log(("✅ " if it["status"] == "ok" else "⚠️ ") + it["label"],
                    level="ok" if it["status"] == "ok" else "warn", stage="prep")
            _sleep(cancel, 0.25)
        if job.get("action") == "check":
            rep.stage_done("prep", "Tout est prêt")
            rep.finish("ok", summary={"checked": True, "ok": True, "mode": mode})
            return
        rep.log("Silences + extraction audio (en parallèle)…", stage="prep")
        tid = rep.task_start("ffmpeg", "ffmpeg · silences + tranches audio")
        _sleep(cancel, 1.6)
        rep.task_end(tid, detail="10 tranches prêtes")
        rep.stage_done("prep", "Prêt")

        if mode == "generate":
            rep.stage_start("derush", total=10, label="Analyse des tranches par Gemini")
            rep.log("10 tranches audio : 7 à analyser (3 déjà en cache), niveau 3/3, 3 en parallèle.", stage="derush")
            _gemini_tasks(rep, cancel, "derush", 7, "Dérush · tranche {i}/{n}",
                          lambda i, n: f"Tranche {i + 3}/10 : {random.randint(18, 40)} segments, {random.randint(4, 12)} à supprimer.",
                          already=3)
            rep.ok("Dérush : 2820 s → 1965 s (214 plans, 30 % supprimé).", stage="derush")
            rep.stage_done("derush", "214 plans · 32.8 min conservées")

        rep.stage_start("speech", label="Transcription par piste")
        rep.log("AgentOMG parle 41 % du temps (212 interventions), Marvouch 37 % (198), les deux en même temps 94 s.", stage="speech")
        _gemini_tasks(rep, cancel, "speech", 6, "Transcription · tranche {i}/{n}",
                      lambda i, n: f"{'AgentOMG' if i % 2 else 'Marvouch'} · tranche {(i + 1) // 2 + 1}/10 : {random.randint(35, 70)} segments.")
        rep.stage_done("speech", "486 répliques")

        rep.stage_start("poses", label="Choix des poses par réplique")
        rep.log("Beats : 486 répliques → 712 beats courts (durée moyenne 2.1 s).", stage="poses")
        _gemini_tasks(rep, cancel, "poses", 5, "Poses · lot {i}/{n}",
                      lambda i, n: f"Poses {i * 30 + 1}–{min(i * 30 + 30, 712)}/712 : 30 choix Gemini")
        rep.ok("Poses : 698 shibis, 22/22 poses différentes, durée moyenne 2.2 s.", stage="poses")
        rep.stage_done("poses", "Poses choisies")

        rep.stage_start("capcut", label="Écriture du projet")
        rep.log("Sauvegarde de l'état actuel : …/_autoshibi_backups/20261022-143000", stage="capcut")
        _sleep(cancel, 1.2)
        rep.log("Zooms : 96 zooms + 78 dézooms sur 214 plans (keyframes : appris depuis le projet)", stage="capcut")
        rep.log("Shibis posés : {'AgentOMG': 356, 'Marvouch': 342} (2 nouvelles pistes au-dessus des tiennes)", stage="capcut")
        rep.stage_done("capcut", "Projet prêt")
        name = (job.get("project") or "AutoShibi-V4-Gen-REC-20261022") if mode == "edit" else "AutoShibi-V4-Gen-REC_2026-10-22-143000"
        rep.ok(f"✅ Projet « {name} » prêt : ouvre CapCut et sélectionne-le.")
        rep.finish("ok", summary={"mode": mode, "project": name, "plans": 214, "segments": 486,
                                  "duration_before_s": 2820, "duration_after_s": 1965,
                                  "zooms": 96, "dezooms": 78,
                                  "shibis": {"AgentOMG": 356, "Marvouch": 342},
                                  "gemini": {"calls": 18, "retries": 1, "uploads": 13}})
    except Cancelled:
        rep.warn("Arrêté (démo). En vrai, tout ce qui est déjà analysé resterait en cache.")
        rep.finish("cancelled", error="Arrêté par l'utilisateur.")
        raise
    finally:
        hb.stop()
