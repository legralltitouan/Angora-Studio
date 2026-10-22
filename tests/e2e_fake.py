"""
Test de bout en bout SANS clé Gemini ni CapCut :
- une fausse IA répond à la place de Gemini (mêmes formats JSON),
- une vraie vidéo synthétique (3 pistes audio) est traitée par ffmpeg/VAD,
- de faux projets CapCut (modèle + projet monté à la main) reçoivent
  dérush, zooms et shibis ; on vérifie le contenu des draft_content.json.

Lancer :  python tests/e2e_fake.py     (ffmpeg requis)
"""
import os
import sys
import json
import uuid
import zlib
import struct
import shutil
import tempfile
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["GEMINI_API_KEY"] = "cle-de-test"

from autoshibi import config, media, gemini as gm, pipeline as pipe, capcut, projedit, derush
from autoshibi.events import Reporter, CancelToken

OK = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {extra}" if extra else ""))
    OK.append(bool(cond))
    if not cond:
        raise AssertionError(name)


# ------------------------------------------------------------------ faux Gemini
class _State:
    name = "ACTIVE"


class _File:
    def __init__(self, name):
        self.name, self.state = name, _State()


class FakeClient:
    def __init__(self):
        self._paths = {}
        self.files = self._Files(self)
        self.models = self._Models(self)

    class _Files:
        def __init__(self, parent):
            self.p = parent

        def upload(self, file):
            name = "files/" + os.path.basename(file)
            self.p._paths[name] = file
            return _File(name)

        def get(self, name):
            return _File(name)

        def delete(self, name):
            pass

    class _Models:
        def __init__(self, parent):
            self.p = parent

        def generate_content(self, model, contents, config=None):
            prompt, path = "", None
            if isinstance(contents, str):
                prompt = contents
            elif isinstance(contents, list):
                for c in contents:
                    if isinstance(c, str):
                        prompt = c
                    elif hasattr(c, "name"):
                        path = self.p._paths.get(c.name)
            else:  # types.Content (profil de style)
                prompt = " ".join(getattr(pt, "text", "") or "" for pt in getattr(contents, "parts", []))
            dur = media.probe_duration(path) if path else 300
            if "Découpe TOUT l'extrait" in prompt:                      # dérush
                segs, t = [], 0.0
                plan = [(0.45, True), (0.15, False), (0.40, True)]
                for frac, keep in plan:
                    e = t + dur * frac
                    segs.append({"start": round(t, 1), "end": round(e, 1), "keep": keep,
                                 "why": "" if keep else "temps mort"})
                    t = e
                text = json.dumps(segs)
            elif "micro d'UNE SEULE personne : AgentOMG" in prompt:     # transcription piste OMG
                text = json.dumps([
                    {"start_time": 1.0, "end_time": 5.0, "text": "Salut tout le monde, on part explorer !",
                     "emotion": "joyeux", "intensity": 2, "intent": "salut"},
                    {"start_time": 10.0, "end_time": 14.0, "text": "Oh le truc de fou, regarde ça !",
                     "emotion": "excité", "intensity": 3, "intent": "réaction"}])
            elif "micro d'UNE SEULE personne : Marvouch" in prompt:      # transcription piste Marvouch
                text = json.dumps([
                    {"start_time": 6.0, "end_time": 9.0, "text": "Attends, tu es sûr de ton coup là ?",
                     "emotion": "curieux", "intensity": 1, "intent": "question"},
                    {"start_time": 15.0, "end_time": 19.0, "text": "Non mais il m'a eu, l'arnaque totale",
                     "emotion": "amusé", "intensity": 2, "intent": "blague"}])
            elif "Poses disponibles" in prompt:                          # choix des poses
                import re as _re
                idx = sorted({int(m) for m in _re.findall(r"(?m)^(\d+) \|", prompt)})
                text = json.dumps([{"i": i, "png_ids": [12, 2, 16]} for i in idx])
            elif "avg_shot_duration_s" in prompt:                        # profil de style
                text = json.dumps({"avg_shot_duration_s": 4.0, "max_pause_kept_s": 0.6,
                                   "removes": "temps morts", "keeps": "rires", "notes": "rythmé"})
            else:
                text = "[]"
            return type("R", (), {"text": text})()


def fake_runner(rep, cancel, workers):
    return gm.Runner(FakeClient(), ["fake-model"], rep, cancel, workers=2)


# ------------------------------------------------------------------ fabrication
def tiny_png(path, rgb=(76, 183, 130)):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * 64 for _ in range(64))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


def make_video(path):
    omg = "sine=frequency=440:sample_rate=16000:duration=24,volume='if(between(t,1,5)+between(t,10,14),0.6,0)':eval=frame"
    Marv = "sine=frequency=660:sample_rate=16000:duration=24,volume='if(between(t,6,9)+between(t,15,19),0.6,0)':eval=frame"
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=24",
           "-f", "lavfi", "-i", omg,
           "-f", "lavfi", "-i", Marv,
           "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first[mix]",
           "-map", "0:v", "-map", "[mix]", "-map", "1:a", "-map", "2:a",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path]
    subprocess.run(cmd, check=True)


def kf_sample(dur_us=24_000_000):
    def pt(off, v):
        return {"curveType": "Line", "graphicCurveType": "linear", "id": str(uuid.uuid4()).upper(),
                "left_control": {"x": 0.0, "y": 0.0}, "right_control": {"x": 0.0, "y": 0.0},
                "string_value": "", "time_offset": off, "values": [v]}
    return [{"id": str(uuid.uuid4()).upper(), "material_id": "", "property_type": p,
             "keyframe_list": [pt(0, 1.0), pt(dur_us - 33333, 1.1)]}
            for p in ("KFTypeScaleX", "KFTypeScaleY")]


def segment(mid, src0, dur, tl0, with_kf=False):
    return {"id": str(uuid.uuid4()).upper(), "material_id": mid,
            "source_timerange": {"start": src0, "duration": dur},
            "target_timerange": {"start": tl0, "duration": dur},
            "clip": {"alpha": 1.0, "rotation": 0.0, "flip": {"horizontal": False, "vertical": False},
                     "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}},
            "common_keyframes": kf_sample(dur) if with_kf else [],
            "extra_material_refs": ["SPD1", "CNV1"], "render_index": 0, "visible": True}


def draft_json(video_path, clips, with_kf=False):
    tl = 0
    segs = []
    for src0, dur in clips:
        segs.append(segment("VID1", src0, dur, tl, with_kf))
        tl += dur
    return {"id": str(uuid.uuid4()).upper(), "canvas_config": {"width": 1920, "height": 1080},
            "duration": tl,
            "materials": {"videos": [{"id": "VID1", "material_id": "VID1", "type": "video",
                                      "path": video_path.replace("\\", "/"),
                                      "material_name": os.path.basename(video_path),
                                      "duration": 24_000_000, "width": 640, "height": 360}],
                          "speeds": [{"id": "SPD1", "speed": 1.0}],
                          "canvases": [{"id": "CNV1"}]},
            "tracks": [{"id": "T0", "type": "video", "segments": segs},
                       {"id": "T1", "type": "video", "segments": []},
                       {"id": "T2", "type": "video", "segments": []}]}


def write_project(base, name, draft, timeline=True):
    pdir = os.path.join(base, name)
    os.makedirs(pdir)
    pid, tid = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
    with open(os.path.join(pdir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        json.dump({"draft_name": name, "draft_fold_path": pdir, "draft_id": pid}, f)
    spots = [os.path.join(pdir, "draft_content.json")]
    if timeline:
        tld = os.path.join(pdir, "Timelines", tid)
        os.makedirs(tld)
        spots.append(os.path.join(tld, "draft_content.json"))
        with open(os.path.join(pdir, "Timelines", "project.json"), "w") as f:
            json.dump({"main_timeline_id": tid, "id": pid}, f)
    for s in spots:
        with open(s, "w", encoding="utf-8") as f:
            json.dump(draft, f, separators=(",", ":"))
    return pdir, pid, tid


def main():
    tmp = tempfile.mkdtemp(prefix="autoshibi_test_")
    made_assets, style_path = False, config.STYLE_PATH
    had_style = os.path.exists(style_path)
    try:
        print(f"[setup] {tmp}")
        # assets temporaires dans le vrai dossier Assets (retirés à la fin)
        for spk, rgb in (("AgentOMG", (76, 183, 130)), ("Marvouch", (91, 141, 239))):
            d = config.ASSET_DIRS[spk]
            os.makedirs(d, exist_ok=True)
            for i in range(1, 23):
                tiny_png(os.path.join(d, f"{i}.png"), rgb)
            made_assets = True

        video = os.path.join(tmp, "rec_test.mkv")
        make_video(video)
        info = media.probe(video)
        check("probe : 3 pistes audio", len(info["audio_streams"]) == 3, str(info["audio_streams"]))
        check("probe : durée ~24 s", 23 < info["duration"] < 25, str(info["duration"]))

        cache = media.cache_dir_for(video)
        speech = media.speech_intervals(video, {"AgentOMG": 1, "Marvouch": 2}, cache)
        om = media.overlap_total(speech["AgentOMG"], [(1, 5)])
        mv = media.overlap_total(speech["Marvouch"], [(15, 19)])
        check("VAD : voix OMG détectée 1-5 s", om > 3.0, f"{om:.1f} s")
        check("VAD : voix Marvouch détectée 15-19 s", mv > 3.0, f"{mv:.1f} s")
        check("VAD : pas de confusion OMG/Marvouch", media.overlap_total(speech["AgentOMG"], [(15, 19)]) < 0.3)

        chunks = media.extract_audio_chunks(video, 0, cache)
        check("extraction : 1 tranche avec offset 0", len(chunks) == 1 and chunks[0].offset == 0.0)
        sil = media.detect_silences(video, info["duration"], cache)
        check("silences détectés", len(sil) >= 2, f"{len(sil)}")

        # projets CapCut factices
        projects = os.path.join(tmp, "com.lveditor.draft")
        os.makedirs(projects)
        tpl_draft = draft_json("C:/old/modele.mp4", [(0, 24_000_000)], with_kf=True)
        tpl_dir, tpl_pid, tpl_tid = write_project(projects, "AutoShibi-V4", tpl_draft)
        edit_draft = draft_json(video, [(0, 10_000_000), (12_000_000, 8_000_000), (1_000_000, 4_000_000)])
        mine_dir, _, _ = write_project(projects, "MonMontage", edit_draft)
        with open(os.path.join(projects, "root_meta_info.json"), "w") as f:
            json.dump({"all_draft_store": [
                {"draft_id": tpl_pid, "draft_name": "AutoShibi-V4", "draft_fold_path": tpl_dir,
                 "draft_json_file": os.path.join(tpl_dir, "Timelines", tpl_tid, "draft_content.json"),
                 "tm_draft_create": 1, "tm_draft_modified": 1}]}, f)

        pipe._runner = fake_runner

        def run(job):
            rep = Reporter(pipe.stages_for(job))
            logs = []

            def _sink(ev):
                if ev["type"] == "log":
                    logs.append(ev)
                    print("   ·", ev["msg"])
            rep.subscribe(_sink)
            try:
                summary = pipe.run_job(job, rep, CancelToken())
            except Exception:
                if rep.result and rep.result[3]:
                    print(rep.result[3])
                raise
            return summary, rep, logs

        base_job = config.default_job()
        base_job.update({"projects_dir": projects, "close_capcut": False, "workers": 2,
                         "template": "AutoShibi-V4", "max_shot_s": 7.0})

        # -------- génération
        print("[génération]")
        job = dict(base_job, mode="generate", action="run", video=video)
        summary, rep, _ = run(job)
        new_dir = summary["project_dir"]
        check("projet cloné créé", os.path.isdir(new_dir) and "Gen" in os.path.basename(new_dir))
        check("UUID du clone renouvelé (nouveau nom de dossier timeline)",
              os.listdir(os.path.join(new_dir, "Timelines"))[0] !=
              os.listdir(os.path.join(tpl_dir, "Timelines"))[0])
        reg = json.load(open(os.path.join(projects, "root_meta_info.json")))
        check("registre CapCut mis à jour", reg["all_draft_store"][0]["draft_name"] == summary["project"])
        gen_files = capcut.draft_files(new_dir)
        gen = capcut.load_draft(gen_files[0])
        main = derush.find_main_track(gen)
        check("timeline : plans découpés", len(main["segments"]) >= 2, f"{len(main['segments'])} plans")
        kfs = sum(1 for s in main["segments"] if s.get("common_keyframes"))
        check("timeline : zooms animés présents", kfs >= 1, f"{kfs} plan(s) animé(s)")
        vid = next(m for m in gen["materials"]["videos"] if m.get("type") == "video")
        check("vidéo retargetée", vid["path"] == video.replace("\\", "/"))
        counts = summary["shibis"]
        check("shibis des 2 personnages", counts["AgentOMG"] > 0 and counts["Marvouch"] > 0, str(counts))
        photos = [m for m in gen["materials"]["videos"] if m.get("type") == "photo"]
        check("matériaux photo valides", all(os.path.exists(m["path"]) for m in photos), f"{len(photos)}")
        check("durée réduite par le dérush", summary["duration_after_s"] < summary["duration_before_s"],
              f"{summary['duration_before_s']} -> {summary['duration_after_s']}")
        check("progression finale = 100 %", abs(rep.progress() - 1.0) < 1e-6, str(rep.progress()))

        # -------- édition d'un projet existant (x2 : remplacement)
        print("[édition]")
        for tour in (1, 2):
            # tour 1 : sur une copie ; tour 2 : sur cette copie, en place
            # (les shibis du tour 1 doivent être remplacés, pas doublés)
            job_e = dict(base_job, mode="edit", action="run",
                         project=("MonMontage" if tour == 1 else base_job["project"]), video="",
                         work_on_copy=(tour == 1))
            s_e, _, _ = run(job_e)
            target = s_e["project_dir"]
            d_e = capcut.load_draft(capcut.draft_files(target)[0])
            info_e = projedit.summarize(d_e)
            check(f"édition tour {tour} : 3 plans d'origine intacts", info_e["clips"] == 3)
            check(f"édition tour {tour} : zooms ajoutés",
                  info_e["with_scale_keyframes"] >= 1, str(info_e["with_scale_keyframes"]))
            check(f"édition tour {tour} : exactement 2 pistes shibis",
                  info_e["previous_shibi_tracks"] == 2, str(info_e["previous_shibi_tracks"]))
            check(f"édition tour {tour} : sauvegarde créée",
                  os.path.isdir(s_e["backup"]), s_e["backup"])
            if tour == 1:
                # la copie devient le projet de référence du tour 2
                base_job = dict(base_job, project=os.path.basename(target))
                job = job_e

        print(f"\nTOUT PASSE ({sum(OK)}/{len(OK)})")
    finally:
        if made_assets:
            for spk in ("AgentOMG", "Marvouch"):
                shutil.rmtree(config.ASSET_DIRS[spk], ignore_errors=True)
        if not had_style and os.path.exists(style_path):
            os.remove(style_path)
        # caches de test créés dans le dossier du logiciel
        for d in os.listdir(config.CACHE_ROOT) if os.path.isdir(config.CACHE_ROOT) else []:
            if d.startswith("rec_test-"):
                shutil.rmtree(os.path.join(config.CACHE_ROOT, d), ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
