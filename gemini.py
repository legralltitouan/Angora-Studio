"""
gemini.py — Client Gemini « réactif ».

Optimisations par rapport à la v1 (mêmes modèles, mêmes prompts) :
- sortie JSON structurée demandée au modèle (bien moins de réponses à
  « réparer »), avec secours automatique si un modèle la refuse ;
- santé des modèles suivie pendant le job : un modèle en 404 est écarté
  pour toute la session au lieu d'être retenté à chaque tranche ; les 429
  déclenchent un vrai backoff exponentiel (et non un sleep fixe de 2 s),
  puis on passe au modèle suivant ;
- chaque appel est une « tâche » visible dans l'interface (envoi, attente
  Google, génération, nouvelle tentative...) avec son chronomètre ;
- timeouts par requête : un appel coincé ne bloque plus tout le montage ;
- exécuteur parallèle générique avec sauvegarde du cache APRÈS CHAQUE
  tranche et reprise exacte là où on s'était arrêté.
"""
import os
import re
import json
import time
import random
import threading
import concurrent.futures as cf

from . import config
from .events import Cancelled


def make_client(timeout_s=600):
    """Client google-genai. Import paresseux : le reste du logiciel démarre sans."""
    from google import genai
    from google.genai import types
    key = config.gemini_api_key()
    if not key:
        raise RuntimeError("Clé Gemini manquante : renseigne-la dans l'interface "
                           "(Réglages avancés) ou dans le fichier .env.")
    try:
        return genai.Client(api_key=key,
                            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
    except Exception:
        return genai.Client(api_key=key)


def parse_json(text, kind="array"):
    """JSON d'une réponse ; récupération des objets valides si elle est tronquée
    (même stratégie éprouvée que la v1)."""
    text = text or ""
    m = re.search(r"\[.*\]" if kind == "array" else r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    if kind == "array":
        objs = []
        for mo in re.finditer(r"\{[^{}]*\}", text):
            try:
                objs.append(json.loads(mo.group(0)))
            except json.JSONDecodeError:
                continue
        return objs or None
    return None


def _classify(err):
    s = str(err)
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code == 404 or "NOT_FOUND" in s or "404" in s:
        return "not_found"
    if code == 429 or "RESOURCE_EXHAUSTED" in s or "429" in s or "quota" in s.lower():
        return "quota"
    if (isinstance(code, int) and code >= 500) or "503" in s or "UNAVAILABLE" in s or "overloaded" in s.lower():
        return "server"
    if "CERTIFICATE" in s or "ConnectError" in s or "ConnectTimeout" in s or "getaddrinfo" in s:
        return "network"
    if "deadline" in s.lower() or "timed out" in s.lower() or "timeout" in s.lower():
        return "timeout"
    return "other"


class ModelPool:
    """Cascade de modèles avec mémoire : 404 → écarté ; 429 → refroidi."""

    def __init__(self, models):
        self.models = list(models)
        self.dead = set()
        self.cool_until = {}
        self._lock = threading.Lock()

    def healthy(self, model):
        with self._lock:
            return model not in self.dead and time.time() >= self.cool_until.get(model, 0)

    def kill(self, model):
        with self._lock:
            self.dead.add(model)

    def cool(self, model, seconds):
        with self._lock:
            self.cool_until[model] = max(self.cool_until.get(model, 0), time.time() + seconds)

    def available(self):
        return [m for m in self.models if self.healthy(m)]


class Runner:
    """Envoie des requêtes à Gemini en tenant l'interface informée en permanence."""

    def __init__(self, client, models, rep, cancel, workers=3, stage=None):
        self.client = client
        self.pool = ModelPool(models)
        self.rep = rep
        self.cancel = cancel
        self.workers = max(1, min(6, int(workers)))
        self.stage = stage
        self.stats = {"calls": 0, "retries": 0, "uploads": 0}
        self._lock = threading.Lock()

    # -------------------------------------------------------------- upload
    def upload(self, path, tid, label=""):
        size = os.path.getsize(path) if os.path.exists(path) else 0
        self.rep.task_update(tid, f"{label} — envoi à Google ({size / 1e6:.1f} Mo)…")
        f = self.client.files.upload(file=path)
        with self._lock:
            self.stats["uploads"] += 1
        t0 = time.time()
        while getattr(f.state, "name", "") == "PROCESSING":
            self.cancel.sleep(2)
            self.rep.task_update(tid, f"{label} — Google prépare le fichier ({time.time() - t0:.0f} s)…")
            f = self.client.files.get(name=f.name)
        if getattr(f.state, "name", "") == "FAILED":
            raise RuntimeError("Google n'a pas pu traiter le fichier envoyé.")
        return f

    def delete(self, f):
        try:
            self.client.files.delete(name=f.name)
        except Exception:
            pass

    # -------------------------------------------------------------- génération JSON
    def generate_json(self, contents, kind="array", tid=None, label="", temperature=0.3):
        """
        Demande du JSON à la cascade de modèles. Retourne les données parsées
        ou None si aucun modèle n'a donné de réponse exploitable.
        Lève l'erreur réseau d'origine si TOUT a échoué pour raison réseau
        (message clair côté utilisateur plutôt qu'un résultat vide muet).
        """
        from google.genai import types as gtypes

        def attempt(model, structured):
            kwargs = {"model": model, "contents": contents}
            if structured:
                kwargs["config"] = gtypes.GenerateContentConfig(
                    response_mime_type="application/json", temperature=temperature)
            return self.client.models.generate_content(**kwargs)

        last_err, passes = None, 0
        while passes < 2:                       # 2e passage si tout était en 429/5xx
            passes += 1
            tried_any = False
            for model in self.pool.available():
                self.cancel.check()
                tried_any = True
                if tid:
                    self.rep.task_update(tid, f"{label} — {model} réfléchit…")
                with self._lock:
                    self.stats["calls"] += 1
                try:
                    try:
                        resp = attempt(model, structured=True)
                    except Exception as e:
                        if _classify(e) in ("not_found", "quota", "server", "network", "timeout"):
                            raise
                        resp = attempt(model, structured=False)   # config refusée : mode plainte
                    data = parse_json(getattr(resp, "text", ""), kind)
                    if data is not None:
                        return data
                    if tid:
                        self.rep.task_update(tid, f"{label} — réponse illisible de {model}, modèle suivant…")
                except Cancelled:
                    raise
                except Exception as e:
                    last_err = e
                    kind_err = _classify(e)
                    with self._lock:
                        self.stats["retries"] += 1
                    if kind_err == "not_found":
                        self.pool.kill(model)
                        self.rep.warn(f"Modèle {model} introuvable (404) : écarté pour ce montage.")
                        continue
                    if kind_err == "quota":
                        back = min(20.0, 4.0 * passes) + random.uniform(0, 1.5)
                        self.pool.cool(model, back * 2)
                        if tid:
                            self.rep.task_update(tid, f"{label} — quota atteint sur {model}, "
                                                      f"pause {back:.0f} s puis modèle suivant…")
                        self.cancel.sleep(back)
                        continue
                    if kind_err == "network":
                        raise
                    if tid:
                        self.rep.task_update(tid, f"{label} — {model} KO ({str(e)[:70]}), modèle suivant…")
                    self.cancel.sleep(1.5)
            if not tried_any:                    # tout est en refroidissement : on attend un peu
                if tid:
                    self.rep.task_update(tid, f"{label} — tous les modèles soufflent (quota), on patiente…")
                self.cancel.sleep(8)
            if passes == 1 and last_err is not None and _classify(last_err) in ("quota", "server", "timeout"):
                continue
            break
        if last_err is not None and _classify(last_err) == "network":
            raise last_err
        return None

    # -------------------------------------------------------------- parallélisme par tranches
    def run_parallel(self, items, worker, on_done=None):
        """
        Exécute worker(item) -> résultat pour chaque item, `workers` en parallèle.
        on_done(item, result) est appelé (thread principal) dès qu'un item finit :
        parfait pour sauvegarder le cache et faire avancer la progression.
        Retourne la première erreur rencontrée (ou None). Annulation coopérative :
        les items non commencés sont abandonnés, ceux en cours se terminent.
        """
        if not items:
            return None
        first_error = None
        with cf.ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(worker, it): it for it in items}
            try:
                for fut in cf.as_completed(futs):
                    it = futs[fut]
                    try:
                        result = fut.result()
                    except Cancelled:
                        raise
                    except Exception as e:            # erreur d'une tranche : notée, les autres continuent
                        first_error = first_error or e
                        result = None
                    if on_done:
                        on_done(it, result)
                    self.cancel.check()
            except Cancelled:
                for f in futs:
                    f.cancel()
                raise
        return first_error


# ------------------------------------------------------------------ cache par tranches
def load_chunk_cache(path, key):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("key") == key:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"key": key, "chunks": {}}


_save_lock = threading.Lock()


def save_chunk_cache(path, cache):
    if not path:
        return
    with _save_lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, path)


def prompt_key(*parts):
    import hashlib
    return hashlib.sha1("␟".join(str(p) for p in parts).encode()).hexdigest()[:12]
