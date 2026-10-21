from __future__ import annotations

import math
from copy import deepcopy
import os
import shutil
import tempfile
import threading
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, butter, sosfilt

from .contracts import AssetRecord, Point
from .io import digest, file_hash, native, under

_processing_guard = threading.Lock()
_processing_locks = {}


def effects(a, rate: int, parameters: dict):
    for key, kind in (("bandpass_hz", "bandpass"), ("lowpass_hz", "lowpass"), ("highpass_hz", "highpass")):
        cutoff = parameters.get(key)
        if cutoff is None:
            continue
        values = cutoff if isinstance(cutoff, (list, tuple)) else [cutoff]
        if any(not isinstance(v, (int, float)) or not 0 < v < rate / 2 for v in values) or kind == "bandpass" and (len(values) != 2 or values[0] >= values[1]):
            raise ValueError("Invalid filter cutoff")
        a = sosfilt(butter(4, cutoff, btype=kind, fs=rate, output="sos"), a, axis=0).astype("float32")
    if parameters.get("bass_gain_db"):
        cutoff = parameters.get("bass_crossover_hz", 200)
        if not 0 < cutoff < rate / 2:
            raise ValueError("Invalid bass crossover")
        low = sosfilt(butter(2, cutoff, fs=rate, output="sos"), a, axis=0)
        a = (a + low * (10 ** (parameters["bass_gain_db"] / 20) - 1)).astype("float32")
    factor = parameters.get("rate_factor", 1)
    if not isinstance(factor, (int, float)) or not .25 <= factor <= 4:
        raise ValueError("Invalid rate factor")
    if factor != 1:
        fraction = Fraction(1 / factor).limit_denominator(10000)
        a = resample_poly(a, fraction.numerator, fraction.denominator, axis=0).astype("float32")
    for key, fade_out in (("fade_in_ms", False), ("fade_out_ms", True)):
        value = parameters.get(key, 0)
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError("Invalid fade duration")
        n = min(len(a), round(value * rate / 1000))
        if n:
            ramp = np.linspace(1, 0, n) if fade_out else np.linspace(0, 1, n)
            if fade_out:
                a[-n:] *= ramp[:, None]
            else:
                a[:n] *= ramp[:, None]
    if parameters.get("normalize_peak_db") is not None:
        level = parameters["normalize_peak_db"]
        if not isinstance(level, (int, float)) or not -80 <= level <= 0:
            raise ValueError("Invalid normalization peak")
        peak = float(np.abs(a).max())
        if peak:
            a = a * (10 ** (level / 20) / peak)
    return a


def detect_onset(a, rate: int, spec: dict):
    left = round(spec.get("search_start_ms", 0) * rate / 1000)
    right = round(spec.get("search_end_ms", len(a) * 1000 / rate) * rate / 1000)
    run = max(1, round(spec.get("min_run_ms", 2) * rate / 1000))
    if not 0 <= left < right <= len(a):
        raise ValueError("Invalid keypoint search window")
    active = np.max(np.abs(a[left:right]), axis=1) >= 10 ** (spec.get("threshold_db", -35) / 20)
    changes = np.diff(np.r_[False, active, False].astype(int))
    starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
    candidates = starts[ends - starts >= run]
    if not len(candidates):
        raise ValueError("No onset candidate in the specified source layer/window")
    return Point(name=spec.get("name", "contact"), frame=left + int(candidates[0]), sample_rate=rate,
                 method="threshold_onset_in_selected_asset", status="candidate").model_dump()


def read(path: Path, rate: int | None = None, channels: int = 1):
    a, sr = sf.read(native(path), dtype="float32", always_2d=True)
    if not len(a) or not np.isfinite(a).all():
        raise ValueError("Empty or non-finite media")
    if channels == 1:
        a = a.mean(axis=1, keepdims=True)
    elif a.shape[1] == 1 and channels == 2:
        a = np.repeat(a, 2, axis=1)
    elif a.shape[1] != channels:
        raise ValueError("Unsupported channel mapping")
    if rate and rate != sr:
        d = math.gcd(sr, rate)
        a = resample_poly(a, rate // d, sr // d, axis=0)
        sr = rate
    return a, sr


def measure(path: Path, root: Path):
    a, sr = read(path, channels=sf.info(native(path)).channels)
    return dict(file=path.relative_to(root).as_posix(), sha256=file_hash(path), frames=len(a),
                sample_rate=sr, channels=a.shape[1], duration_ms=len(a) * 1000 / sr,
                peak=float(np.abs(a).max()), clipped_fraction=float(np.mean(np.abs(a) >= .999969)))


def write(path: Path, a, rate: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".wav")
    os.close(fd)
    try:
        sf.write(temp, a, rate, subtype="PCM_24")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def store_raw(root: Path, path: Path):
    sha = file_hash(path)
    dest = root / "assets" / "raw" / f"{sha[:32]}{path.suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copyfile(path, dest)
    if file_hash(dest) != sha:
        raise ValueError("Raw asset collision or corruption")
    return measure(dest, root)


def compose(root: Path, layers: list[dict], rate: int, channels: int):
    """Composition uses measured frame counts; sequence/count do not depend on a model."""
    pieces, lineage, points = [], [], []
    cursor = 0
    for index, layer in enumerate(layers):
        source = Path(layer["file"])
        raw = store_raw(root, source)
        a, _ = read(source, rate, channels)
        offset = round(layer.get("offset_ms", cursor * 1000 / rate) * rate / 1000)
        gap = round(layer.get("gap_ms", 0) * rate / 1000)
        count = layer.get("repeat", 1)
        if not isinstance(count, int) or count < 1 or count > 10000 or offset < 0 or gap < 0:
            raise ValueError("Invalid composition count/offset/gap")
        a = a * 10 ** (layer.get("gain_db", 0) / 20)
        fade_in = min(len(a), round(layer.get("fade_in_ms", 0) * rate / 1000))
        fade_out = min(len(a), round(layer.get("fade_out_ms", 0) * rate / 1000))
        if fade_in:
            a[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
        if fade_out:
            a[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
        for occurrence in range(count):
            start = offset + occurrence * (len(a) + gap)
            pieces.append((start, a))
            for p in layer.get("keypoints", []):
                p = Point.model_validate(p)
                frame = round(p.frame * rate / p.sample_rate)
                if frame >= len(a):
                    raise ValueError("Layer keypoint outside measured asset")
                points.append(p.model_copy(update={"name": f"layer{index + 1}.{occurrence + 1}.{p.name}", "frame": start + frame, "sample_rate": rate}).model_dump())
        cursor = max(cursor, offset + count * len(a) + (count - 1) * gap)
        lineage.append(raw | {"recipe": {k: v for k, v in layer.items() if k != "file"}})
    if not pieces:
        raise ValueError("Empty composition recipe")
    output = np.zeros((cursor, channels), dtype="float32")
    for start, a in pieces:
        output[start:start + len(a)] += a
    if np.abs(output).max() > 1:
        raise ValueError("Composition clips; adjust layer gain explicitly")
    dest = root / "assets" / "composed" / f"{digest(lineage)[:32]}.wav"
    write(dest, output, rate)
    return measure(dest, root) | {"recipe": lineage, "keypoints": points}


def process(root: Path, task: dict, raw: dict, config: dict):
    rate = config.get("sample_rate", 48000)
    channels = config.get("channels", 1)
    processing = dict(config.get("processing", {})) | task["payload"].get("processing", {})
    a, _ = read(under(root, raw["file"]), rate, channels)
    original_frames = len(a)
    points = [Point.model_validate(p) for p in (task["payload"].get("keypoints") or raw.get("keypoints", []))]
    converted = [p.model_copy(update={"frame": round(p.frame * rate / p.sample_rate), "sample_rate": rate}) for p in points]
    start, end = 0, len(a)
    if processing.get("trim_enabled"):
        if not processing.get("trim_confirmed"):
            raise ValueError("Boundary silence trimming needs explicit confirmation")
        threshold = 10 ** (processing.get("silence_threshold_db", -65) / 20)
        active = np.flatnonzero(np.max(np.abs(a), axis=1) > threshold)
        if not len(active):
            raise ValueError("Cannot silently trim an all-silent asset")
        pad = round(processing.get("padding_ms", 20) * rate / 1000)
        start, end = max(0, int(active[0]) - pad), min(len(a), int(active[-1]) + 1 + pad)
    for p in converted:
        if p.frame < start or p.frame >= end:
            raise ValueError(f"Keypoint {p.name} removed or outside measured media")
    if processing.get("crop_start_ms") is not None:
        start = max(start, round(processing["crop_start_ms"] * rate / 1000))
    if processing.get("crop_end_ms") is not None:
        end = min(end, round(processing["crop_end_ms"] * rate / 1000))
    if not 0 <= start < end <= len(a):
        raise ValueError("Invalid explicit crop")
    if any(p.frame < start or p.frame >= end for p in converted):
        raise ValueError("Crop removes a provided keypoint")
    allowed = {"trim_enabled", "trim_confirmed", "padding_ms", "silence_threshold_db", "gain_db", "crop_start_ms", "crop_end_ms",
               "bandpass_hz", "lowpass_hz", "highpass_hz", "bass_gain_db", "bass_crossover_hz", "rate_factor", "fade_in_ms", "fade_out_ms", "normalize_peak_db", "keypoint_detection"}
    if set(processing) - allowed:
        raise ValueError("Unsupported media processing operation")
    a = effects(a[start:end] * 10 ** (processing.get("gain_db", 0) / 20), rate, processing)
    factor = processing.get("rate_factor", 1)
    converted = [p.model_copy(update={"frame": round((p.frame - start) / factor),
                                     "status": "candidate" if factor != 1 or any(processing.get(k) for k in ("bandpass_hz", "lowpass_hz", "highpass_hz")) else p.status}) for p in converted]
    if processing.get("keypoint_detection"):
        point = Point.model_validate(detect_onset(a, rate, processing["keypoint_detection"]))
        if any(p.name == point.name for p in converted):
            raise ValueError("Duplicate detected keypoint name")
        converted.append(point)
    if np.abs(a).max() > 1:
        raise ValueError("Processing clips; change gain")
    operations = dict(parameters=processing, trim_start_frame=start, trim_end_frame=end, original_resampled_frames=original_frames, target_rate=rate, target_channels=channels)
    revision = digest({"raw": raw["sha256"], "operations": operations})
    dest = root / "assets" / "processed" / f"{revision[:32]}.wav"
    with _processing_guard:
        lock = _processing_locks.setdefault(str(dest), threading.Lock())
    with lock:
        if not dest.exists():
            write(dest, a, rate)
        info = measure(dest, root)
    return AssetRecord(asset_id="asset_" + revision[:24], artifact_revision=revision,
                       task_id=task["task_id"], block_id=task["block_id"], raw=raw,
                       processed=info, processing=operations,
                        keypoints=converted,
                       generation={k: v for k, v in raw.items() if k not in {"file", "sha256", "recipe", "keypoints"}}).model_dump()


def mix(root: Path, timeline: dict, config: dict, dest: Path):
    rate, total = timeline["sample_rate"], timeline["total_frames"]
    channels = config.get("channels", 1)
    output = np.zeros((max(1, total), channels), dtype="float32")
    speech = [(p["start_frame"], p["end_frame"], p.get("render", "dialogue")) for p in timeline["placements"] if p["kind"] == "speech"]
    mix_config = config.get("mix", {})
    duck = mix_config.get("duck", {})
    for p in timeline["placements"]:
        if not p.get("file"):
            continue
        path = under(root, p["file"])
        if file_hash(path) != p["sha256"]:
            raise ValueError("Media changed after timeline calculation")
        a, sr = read(path, rate, channels)
        if len(a) != p["end_frame"] - p["start_frame"]:
            raise ValueError("Timeline duration differs from measured media")
        a *= 10 ** (mix_config.get("gains_db", {}).get(p["block_id"], mix_config.get("kind_gains_db", {}).get(p["kind"], 0)) / 20)
        if duck.get("enabled") and p["kind"] in duck.get("kinds", ["ambience", "music"]):
            envelope = np.ones(len(a), dtype="float32")
            attack = round(duck.get("attack_ms", 30) * rate / 1000)
            release = round(duck.get("release_ms", 100) * rate / 1000)
            for left, right, render in speech:
                level = 10 ** (duck.get("gain_by_render_db", {}).get(render, duck.get("gain_db", -9)) / 20)
                left, right = left - p["start_frame"], right - p["start_frame"]
                for i, j, curve in [(left - attack, left, "attack"), (left, right, "hold"), (right, right + release, "release")]:
                    lo, hi = max(0, i), min(len(a), j)
                    if hi <= lo:
                        continue
                    factors = np.full(j - i, level) if curve == "hold" else np.linspace(1, level, j - i) if curve == "attack" else np.linspace(level, 1, j - i)
                    envelope[lo:hi] = np.minimum(envelope[lo:hi], factors[lo - i:hi - i])
            a *= envelope[:, None]
        output[p["start_frame"]:p["end_frame"]] += a
    output *= 10 ** (mix_config.get("master_gain_db", 0) / 20)
    peak = float(np.abs(output).max())
    ceiling = mix_config.get("peak_ceiling", .95)
    if not 0 < ceiling <= 1:
        raise ValueError("Invalid peak ceiling")
    scale = min(1., ceiling / peak) if mix_config.get("limit", True) and peak else 1.
    if peak > 1 and scale == 1:
        raise ValueError("Mix would clip with limiting disabled")
    write(dest, output * scale, rate)
    return measure(dest, root) | {"peak_reduction_gain": scale, "mix_config": mix_config, "semantic_review": "pending"}


def loop_asset(root: Path, asset: dict, frames: int):
    if frames <= 0:
        raise ValueError("Background loop must have positive length")
    parent = asset["processed"]
    a, rate = read(under(root, parent["file"]), channels=parent["channels"])
    if frames == len(a):
        return asset
    for p in asset["keypoints"]:
        if p["frame"] >= frames and p["status"] == "confirmed":
            raise ValueError("Background crop would remove a confirmed keypoint")
    count = math.ceil(frames / len(a))
    b = np.tile(a, (count, 1))[:frames]
    revision = digest({"parent_sha256": parent["sha256"], "loop_frames": frames, "rate": rate})
    path = root / "assets" / "processed" / (revision[:32] + ".wav")
    if not path.exists():
        write(path, b, rate)
    result = deepcopy(asset)
    result.update(asset_id="asset_" + revision[:24], artifact_revision=revision, processed=measure(path, root))
    result["processing"] = result["processing"] | {"loop_target_frames": frames, "loop_count": count, "loop_parent": parent}
    result["keypoints"] = []
    for occurrence in range(count):
        for p in asset["keypoints"]:
            frame = p["frame"] + occurrence * len(a)
            if frame < frames:
                result["keypoints"].append(p | {"name": p["name"] if occurrence == 0 else f"{p['name']}.{occurrence + 1}", "frame": frame})
    return result
