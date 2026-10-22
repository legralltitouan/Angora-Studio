"""
events.py — Le « pouls » du logiciel.

Toute l'activité du moteur (étapes, progression réelle en unités de travail,
tâches Gemini en cours, journal) passe par un bus d'événements. L'interface
s'y abonne (SSE) et la console aussi ; un fil « heartbeat » émet un signe de
vie toutes les 2 secondes pendant un traitement : l'écran bouge toujours,
même pendant un long appel Gemini — impossible de croire que ça a planté.
"""
import time
import threading
import collections


class Cancelled(Exception):
    """Levée quand l'utilisateur arrête le traitement (arrêt coopératif)."""


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()
        self._procs = set()                  # sous-processus ffmpeg à terminer
        self._lock = threading.Lock()

    def cancel(self):
        self._ev.set()
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass

    def cancelled(self):
        return self._ev.is_set()

    def check(self):
        if self._ev.is_set():
            raise Cancelled()

    def register(self, proc):
        with self._lock:
            self._procs.add(proc)

    def unregister(self, proc):
        with self._lock:
            self._procs.discard(proc)

    def sleep(self, seconds):
        """Sleep interruptible par l'annulation (par petits pas)."""
        end = time.time() + seconds
        while time.time() < end:
            self.check()
            time.sleep(min(0.2, max(0.0, end - time.time())))


class Reporter:
    """
    État d'avancement d'un job + diffusion d'événements.

    La progression globale est calculée à partir d'unités RÉELLES
    (tranches Gemini terminées / total, lots de poses...), pondérées par
    étape — fini la barre qui reste figée 10 minutes sur « 35 % ».
    """

    def __init__(self, stages, sink=None):
        """stages : [(id, titre, poids), ...] — les poids sont normalisés."""
        total = sum(w for _, _, w in stages) or 1.0
        self.stages = [{"id": sid, "title": title, "weight": w / total,
                        "status": "pending", "done": 0, "total": 0, "label": ""}
                       for sid, title, w in stages]
        self.by_id = {s["id"]: s for s in self.stages}
        self.tasks = {}                       # tâches en cours (appels Gemini, ffmpeg...)
        self.history = collections.deque(maxlen=600)
        self.started_at = time.time()
        self.finished = False
        self.result = None                    # ("ok", résumé) | ("error", ...) | ("cancelled",)
        self._subs = []
        self._lock = threading.Lock()
        self._sink = sink                     # ex. affichage console (CLI)
        self._task_seq = 0
        self._unit_times = collections.deque(maxlen=24)   # durées récentes -> ETA

    # ------------------------------------------------------------------ base
    def emit(self, ev):
        ev = dict(ev)
        ev.setdefault("ts", time.time())
        ev["elapsed"] = round(time.time() - self.started_at, 1)
        ev["progress"] = round(self.progress(), 4)
        with self._lock:
            self.history.append(ev)
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(ev)
            except Exception:
                pass
        if self._sink:
            try:
                self._sink(ev)
            except Exception:
                pass

    def subscribe(self, fn):
        with self._lock:
            self._subs.append(fn)

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def progress(self):
        p = 0.0
        for s in self.stages:
            if s["status"] == "done":
                frac = 1.0
            elif s["total"]:
                frac = min(1.0, s["done"] / s["total"])
            elif s["status"] == "running":
                frac = 0.04
            else:
                frac = 0.0
            p += s["weight"] * frac
        return p

    def eta(self):
        """Estimation du temps restant (secondes) ou None. Basée sur le rythme réel."""
        p = self.progress()
        el = time.time() - self.started_at
        if p > 0.03 and el > 5:
            return max(0.0, el * (1 - p) / p)
        return None

    # ------------------------------------------------------------------ journal
    def log(self, msg, level="info", stage=None):
        self.emit({"type": "log", "level": level, "msg": str(msg), "stage": stage})

    def ok(self, msg, stage=None):
        self.log(msg, "ok", stage)

    def warn(self, msg, stage=None):
        self.log(msg, "warn", stage)

    def error(self, msg, stage=None):
        self.log(msg, "err", stage)

    # ------------------------------------------------------------------ étapes
    def stage_start(self, sid, total=0, label=""):
        s = self.by_id.get(sid)
        if s:
            s["status"] = "running"
            s["total"] = total
            s["done"] = 0
            s["label"] = label
        self.emit({"type": "stage", "stage": sid, "status": "running",
                   "done": 0, "total": total, "label": label})

    def stage_units(self, sid, total):
        s = self.by_id.get(sid)
        if s:
            s["total"] = total
        self.emit({"type": "stage", "stage": sid, "status": "running",
                   "done": s["done"] if s else 0, "total": total, "label": s["label"] if s else ""})

    def stage_tick(self, sid, label=None, unit_seconds=None):
        s = self.by_id.get(sid)
        if s:
            s["done"] += 1
            if label is not None:
                s["label"] = label
            if unit_seconds:
                self._unit_times.append(unit_seconds)
            done, total, lbl = s["done"], s["total"], s["label"]
        else:
            done = total = 0
            lbl = label or ""
        self.emit({"type": "stage", "stage": sid, "status": "running",
                   "done": done, "total": total, "label": lbl, "eta": self.eta()})

    def stage_label(self, sid, label):
        s = self.by_id.get(sid)
        if s:
            s["label"] = label
        self.emit({"type": "stage", "stage": sid, "status": "running",
                   "done": s["done"] if s else 0, "total": s["total"] if s else 0, "label": label})

    def stage_done(self, sid, label=None):
        s = self.by_id.get(sid)
        if s:
            s["status"] = "done"
            if s["total"]:
                s["done"] = s["total"]
            if label is not None:
                s["label"] = label
        self.emit({"type": "stage", "stage": sid, "status": "done",
                   "done": s["done"] if s else 0, "total": s["total"] if s else 0,
                   "label": label if label is not None else (s["label"] if s else "")})

    # ------------------------------------------------------------------ tâches en cours
    def task_start(self, kind, label):
        with self._lock:
            self._task_seq += 1
            tid = self._task_seq
        self.tasks[tid] = {"id": tid, "kind": kind, "label": label, "t0": time.time()}
        self.emit({"type": "task", "task": tid, "status": "start", "kind": kind, "label": label})
        return tid

    def task_update(self, tid, label):
        t = self.tasks.get(tid)
        if t:
            t["label"] = label
        self.emit({"type": "task", "task": tid, "status": "update", "label": label})

    def task_end(self, tid, ok=True, detail=""):
        t = self.tasks.pop(tid, None)
        secs = round(time.time() - t["t0"], 1) if t else 0
        self.emit({"type": "task", "task": tid, "status": "end", "ok": ok,
                   "seconds": secs, "detail": detail, "kind": t["kind"] if t else "",
                   "label": t["label"] if t else ""})
        return secs

    # ------------------------------------------------------------------ fin
    def finish(self, status, summary=None, error=None, detail=""):
        self.finished = True
        self.result = (status, summary or {}, error or "", detail or "")
        self.emit({"type": "done", "status": status, "summary": summary or {},
                   "error": error or "", "detail": detail or "",
                   "seconds": round(time.time() - self.started_at, 1)})

    def snapshot(self):
        now = time.time()
        return {
            "stages": [dict(s) for s in self.stages],
            "tasks": [{"id": t["id"], "kind": t["kind"], "label": t["label"],
                       "seconds": round(now - t["t0"], 1)} for t in self.tasks.values()],
            "progress": round(self.progress(), 4),
            "elapsed": round(now - self.started_at, 1),
            "eta": self.eta(),
            "finished": self.finished,
            "result": ({"status": self.result[0], "summary": self.result[1],
                        "error": self.result[2], "detail": self.result[3]} if self.result else None),
        }


class Heartbeat:
    """Émet un signe de vie périodique tant que le job tourne."""

    def __init__(self, reporter, interval=2.0):
        self.rep = reporter
        self.interval = interval
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._th.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            if self.rep.finished:
                break
            snap = self.rep.snapshot()
            self.rep.emit({"type": "heartbeat", "tasks": snap["tasks"],
                           "elapsed": snap["elapsed"], "eta": snap["eta"]})


class SilentReporter(Reporter):
    """Reporter minimal pour les appels hors job (vérifications, outils)."""

    def __init__(self, sink=None):
        super().__init__([("work", "Traitement", 1.0)], sink=sink)
