"""
cli.py — Ligne de commande, compatible avec les drapeaux de la v1
(autocut_shibi.py --check / --video / --level / --fresh / --repick ...).
Elle pilote exactement le même moteur que l'interface.
"""
import os
import sys
import argparse

from . import config, media, pipeline as pipe
from .events import Reporter, CancelToken, Cancelled


def _console_sink(ev):
    if ev["type"] == "log":
        print(ev["msg"], flush=True)
    elif ev["type"] == "stage" and ev["status"] == "done":
        print(f"[{ev['stage']}] terminé — {ev.get('label', '')}", flush=True)
    elif ev["type"] == "done":
        if ev["status"] == "ok" and not (ev.get("summary") or {}).get("checked"):
            print("\n✅ Terminé.", flush=True)
        elif ev["status"] != "ok":
            print(f"\n❌ {ev.get('error', '')}", flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="AutoShibi Studio 2 — génération d'un projet CapCut "
                                            "(dérush + zooms + shibis) à partir d'une vidéo")
    p.add_argument("--video", help="vidéo à monter (défaut : VIDEO_FILE de .env, sinon video.mkv à côté du logiciel)")
    p.add_argument("--check", action="store_true", help="vérifie l'installation, sans rien générer")
    p.add_argument("--no-derush", action="store_true", help="pas de coupes ; on garde tout")
    p.add_argument("--no-zoom", action="store_true", help="désactive les zooms/dézooms")
    p.add_argument("--level", type=int, choices=(1, 2, 3), help="niveau de coupe du dérush : 1 léger, 2 normal, 3 serré")
    p.add_argument("--reuse", action="store_true",
                   help="(comportement par défaut en v2 : les analyses en cache sont toujours réutilisées)")
    p.add_argument("--refresh-style", action="store_true", help="ré-analyse les vidéos de référence (style_profile.json)")
    p.add_argument("--no-tracks", action="store_true", help="n'utilise PAS les pistes audio séparées (Gemini devine)")
    p.add_argument("--fresh", action="store_true", help="efface le cache de CETTE vidéo et refait tout")
    p.add_argument("--repick", action="store_true", help="refait le choix des poses")
    p.add_argument("--template", help="nom du projet CapCut modèle (défaut : AutoShibi-V4)")
    p.add_argument("--workers", type=int, help="requêtes Gemini en parallèle (1-6)")
    p.add_argument("--debug", action="store_true", help="écrit draft_debug.json dans le cache de la vidéo")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    job = config.default_job()
    job.update({"mode": "generate", "action": "check" if args.check else "run",
                "video": args.video or "", "derush": not args.no_derush,
                "zoom": not args.no_zoom,
                "shibi_source": "ai" if args.no_tracks else "tracks",
                "fresh": args.fresh, "repick": args.repick})
    if args.level:
        job["derush_level"] = args.level
    if args.template:
        job["template"] = args.template
    if args.workers:
        job["workers"] = args.workers
    if args.refresh_style:
        try:
            os.remove(config.STYLE_PATH)
            print("Profil de style supprimé : il sera régénéré depuis les vidéos de référence.")
        except OSError:
            pass
    rep = Reporter(pipe.stages_for(job), sink=_console_sink)
    cancel = CancelToken()
    try:
        summary = pipe.run_job(job, rep, cancel)
    except pipe.Fail:
        return 1
    except Cancelled:
        print("\nInterrompu.")
        return 1
    except KeyboardInterrupt:
        cancel.cancel()
        print("\nInterrompu.")
        return 1
    if args.debug and summary.get("project_dir"):
        try:
            from . import capcut, derush
            files = capcut.draft_files(summary["project_dir"])
            video = pipe.resolve_video(job)
            out = os.path.join(media.cache_dir_for(video), "draft_debug.json")
            derush.dump_debug(capcut.load_draft(files[0]), out)
            print(f"Debug : {out}")
        except Exception as e:
            print(f"Debug impossible : {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
