"""Local cut verification. No video rendering and no additional AI calls."""
import array
import copy
import json
import math
import os
import tempfile
import threading
import uuid

from . import media

_LOCK = threading.RLock()
_SESSION = None


def state():
    with _LOCK:
        if not _SESSION or not _SESSION["active"]:
            return {"active": False}
        return {k: copy.deepcopy(v) for k, v in _SESSION.items()
                if k not in ("event", "video", "decision")}


def source_path(session_id):
    with _LOCK:
        if _SESSION and _SESSION["active"] and _SESSION["id"] == session_id:
            return _SESSION["video"]


def validate_ranges(ranges, duration):
    if not isinstance(ranges, list) or len(ranges) > 20000:
        raise ValueError("Liste de coupes invalide.")
    out = []
    for index, pair in enumerate(ranges, 1):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Chaque intervalle doit contenir un début et une fin.")
        a, b = map(float, pair)
        if not all(map(math.isfinite, (a, b))):
            raise ValueError(f"Passage {index} : début ou fin non numérique.")
        # UI minimum is a drag affordance, not a server validity constraint.
        # Existing cuts, restored tiny gaps, and floating-point 40 ms edges
        # are valid too. Never silently drop a positive kept interval.
        if a < -.001 or b > duration + .05:
            raise ValueError(f"Passage {index} hors vidéo : {a:.6f} → {b:.6f} s "
                             f"(durée source : {duration:.6f} s).")
        a, b = max(0., a), min(duration, b)
        if b <= a:
            raise ValueError(f"Passage {index} : la fin doit être après le début "
                             f"({a:.6f} → {b:.6f} s).")
        out.append((a, b))
    out = media.merge_ranges(out)
    if not out:
        raise ValueError("Garde au moins un passage pour créer le montage.")
    return out


def decide(session_id, keep):
    with _LOCK:
        if not _SESSION or not _SESSION["active"] or _SESSION["id"] != session_id:
            return {"ok": False, "error": "Cette vérification n'est plus active."}
        try:
            ranges = validate_ranges(keep, _SESSION["duration"])
        except (ValueError, TypeError, OverflowError) as exc:
            return {"ok": False, "error": str(exc)}
        _SESSION["decision"] = ranges
        _SESSION["active"] = False
        _SESSION["event"].set()
        return {"ok": True}


def _waveform(chunks, duration, cache_dir, cancel):
    path = os.path.join(cache_dir, "waveform-v1.json")
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached["duration"] == duration:
            return cached["peaks"]
    except (OSError, ValueError, KeyError):
        pass
    step = max(.1, duration / 16000)
    peaks = [0] * (int(math.ceil(duration / step)) + 1)
    # Decode the already-extracted light audio, never the full-resolution video.
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "wave.raw")
        for ch in chunks:
            cancel.check()
            result = media._run(["ffmpeg", "-v", "error", "-y", "-i", ch.path,
                "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", raw], cancel=cancel)
            if result.returncode:
                raise RuntimeError("Impossible de décoder la forme d'onde.")
            samples = array.array("h")
            with open(raw, "rb") as f:
                samples.frombytes(f.read())
            import sys
            if sys.byteorder != "little":
                samples.byteswap()
            block = max(1, int(8000 * step))
            for off in range(0, len(samples), block):
                idx = int((ch.offset + off / 8000) / step)
                if idx < len(peaks):
                    peaks[idx] = max(peaks[idx], max((abs(x) for x in samples[off:off+block]), default=0))
    top = max(peaks) or 1
    peaks = [round((x / top) ** .5, 3) for x in peaks]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"duration": duration, "peaks": peaks}, f)
    return peaks


def request(keep, silences, video, duration, chunks, cache_dir, rep, cancel):
    global _SESSION
    session_id, event = uuid.uuid4().hex, threading.Event()
    with _LOCK:
        _SESSION = {"active": True, "id": session_id, "duration": duration,
                    "keep": [(max(0,a), min(duration,b)) for a,b in keep if min(duration,b) > max(0,a)], "silences": list(silences), "peaks": [],
                    "wave_status": "loading", "video": video, "event": event, "decision": None}
    rep.stage_start("verify", label="À toi : vérifier les coupes dans la timeline")
    rep.log("Vérification manuelle : montage en pause AVANT les shibis et zooms. La timeline est disponible immédiatement.")
    rep.emit({"type": "cut_verification", "active": True})

    def wave():
        tid = rep.task_start("ffmpeg", "Forme d'onde audio — édition déjà disponible")
        try:
            peaks = _waveform(chunks, duration, cache_dir, cancel)
            with _LOCK:
                if _SESSION and _SESSION["id"] == session_id:
                    _SESSION.update(peaks=peaks, wave_status="ready")
        except Exception:
            with _LOCK:
                if _SESSION and _SESSION["id"] == session_id:
                    _SESSION["wave_status"] = "unavailable"
        finally:
            rep.task_end(tid)
    if not event.is_set():
        threading.Thread(target=wave, daemon=True).start()
    try:
        while not event.wait(.25):
            cancel.check()
        cancel.check()
        with _LOCK:
            result = _SESSION["decision"]
        rep.stage_done("verify", "Coupes validées — génération sur les passages conservés")
        return result
    finally:
        with _LOCK:
            if _SESSION and _SESSION["id"] == session_id:
                _SESSION["active"] = False
        rep.emit({"type": "cut_verification", "active": False})
