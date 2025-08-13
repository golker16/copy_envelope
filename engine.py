from __future__ import annotations
from pathlib import Path
from typing import List, Callable, Dict, Optional

import numpy as np
import soundfile as sf
import librosa
from scipy.signal import hilbert

# Optional loudness match
try:
    import pyloudnorm as pyln
    _HAS_LOUD = True
except Exception:
    _HAS_LOUD = False

def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)

def lin_to_db(x: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    return 20.0 * np.log10(np.clip(np.abs(x), eps, None))

def load_audio(path: str, sr_target: Optional[int] = None):
    y, sr = librosa.load(path, sr=sr_target, mono=False)  # preserve channels then mix later
    if y.ndim == 1:
        y = y[np.newaxis, :]  # (C=1, N)
    return y, sr

def to_mono(y: np.ndarray) -> np.ndarray:
    if y.ndim == 1:
        return y
    return np.mean(y, axis=0)

def envelope_hilbert(y: np.ndarray) -> np.ndarray:
    # analytic envelope
    return np.abs(hilbert(y))

def envelope_rms(y: np.ndarray, frame: int = 2048, hop: int = 512) -> np.ndarray:
    # frame RMS, then upsample to length
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop, center=True)[0]
    env = librosa.util.fix_length(np.repeat(rms, hop), size=y.shape[-1])
    return env

def smooth_envelope(env: np.ndarray, sr: int, attack_ms: float, release_ms: float) -> np.ndarray:
    atk = max(1, int(sr * (attack_ms / 1000.0)))
    rel = max(1, int(sr * (release_ms / 1000.0)))
    out = np.empty_like(env)
    prev = env[0]
    # attack (rise) smoothing
    for i in range(env.size):
        prev += (env[i] - prev) / atk
        out[i] = prev
    # release (fall) smoothing
    prev = out[-1]
    for i in range(env.size - 1, -1, -1):
        prev += (out[i] - prev) / rel
        out[i] = prev
    return out

def loop_to_length(arr: np.ndarray, length: int) -> np.ndarray:
    if arr.size >= length:
        return arr[:length]
    reps = (length + arr.size - 1) // arr.size
    out = np.tile(arr, reps)[:length]
    return out

def combine_envelopes(envs: List[np.ndarray], mode: str = "max", weights: Optional[List[float]] = None) -> np.ndarray:
    E = np.stack(envs, axis=0)  # (M, N)
    mode = (mode or "max").lower()
    if mode == "max":
        return np.max(E, axis=0)
    if mode == "mean":
        return np.mean(E, axis=0)
    if mode == "geom_mean":
        # avoid zeros
        return np.exp(np.mean(np.log(np.clip(E, 1e-12, None)), axis=0))
    if mode == "product":
        return np.prod(np.clip(E, 1e-6, None), axis=0)
    if mode == "sum_limited":
        s = np.sum(E, axis=0)
        # limit to 1.5x of max single env to avoid overboost
        limit = np.max(E, axis=0) * 1.5
        return np.minimum(s, limit)
    if mode == "weighted":
        if not weights or len(weights) != E.shape[0]:
            raise ValueError("weights length must match number of envelopes when mode='weighted'")
        W = np.array(weights, dtype=np.float64)[:, None]
        return np.sum(E * W, axis=0) / (np.sum(W) + 1e-12)
    # default
    return np.max(E, axis=0)

def match_lufs(target: np.ndarray, sr: int, ref_lufs: float) -> float:
    """Return linear gain to reach ref_lufs from target signal loudness."""
    if not _HAS_LOUD:
        return 1.0
    meter = pyln.Meter(sr)
    loud = meter.integrated_loudness(target.astype(np.float64))
    gain_db = ref_lufs - loud
    return db_to_lin(gain_db)

def apply_envelopes(dest_path: str, mold_paths: list[str], out_path: str, cfg: Dict,
                    progress_cb: Callable[[int], None], log_cb: Callable[[str], None]) -> None:
    """Main pipeline: load destination, build/loop/merge envelopes from molds, apply, write."""
    progress_cb(2); log_cb(f"Destino: {Path(dest_path).name}")
    y_dst, sr = load_audio(dest_path, sr_target=None)  # keep native SR
    y_dst_mono = to_mono(y_dst)
    N = y_dst_mono.shape[-1]
    progress_cb(5)

    # Envelope settings
    mode = (cfg.get("mode") or "hilbert").lower()
    frame = int(cfg.get("frame", 2048))
    hop = int(cfg.get("hop", 512))
    attack_ms = float(cfg.get("attack_ms", 1.0))
    release_ms = float(cfg.get("release_ms", 0.5))
    floor_db = float(cfg.get("floor_db", -40.0))
    combine_mode = (cfg.get("combine_mode") or "max").lower()
    weights = cfg.get("weights", None)
    match_lufs_flag = bool(cfg.get("match_lufs", False))

    # Build envelopes from molds
    envs = []
    total_m = max(1, len(mold_paths))
    for i, p in enumerate(mold_paths, start=1):
        log_cb(f"Cargando molde: {Path(p).name}")
        y_m, sr_m = load_audio(p, sr_target=sr)  # resample to destination SR
        y_mono = to_mono(y_m)

        # raw envelope
        if mode == "rms":
            env = envelope_rms(y_mono, frame=frame, hop=hop)
        else:
            env = envelope_hilbert(y_mono)

        # normalize envelope to [0..1]
        peak = float(np.max(env) + 1e-12)
        env = env / peak

        # smooth
        env = smooth_envelope(env, sr=sr, attack_ms=attack_ms, release_ms=release_ms)

        # loop to destination length
        env = loop_to_length(env, N)

        envs.append(env)
        progress_cb(5 + int(60 * i / total_m))

    if not envs:
        raise RuntimeError("No se encontraron moldes válidos.")

    # combine
    log_cb(f"Combinando envelopes (mode={combine_mode})…")
    E = combine_envelopes(envs, mode=combine_mode, weights=weights)

    # floor in dB (avoid total mute)
    floor_lin = db_to_lin(floor_db)  # negative value, e.g., -40 dB -> small positive lin
    E = np.clip(E, floor_lin, None)

    # apply to destination (per channel)
    y_out = np.empty_like(y_dst, dtype=np.float32)
    for ci in range(y_dst.shape[0]):
        y_out[ci] = (y_dst[ci] * E).astype(np.float32)

    progress_cb(90)

    # optional loudness match: keep destination LUFS
    if match_lufs_flag and _HAS_LOUD:
        meter = pyln.Meter(sr)
        ref_lufs = meter.integrated_loudness(y_dst_mono.astype(np.float64))
        cur_lufs = meter.integrated_loudness(to_mono(y_out).astype(np.float64))
        gain_db = ref_lufs - cur_lufs
        g = db_to_lin(gain_db)
        y_out = (y_out * g).astype(np.float32)
        log_cb(f"LUFS match: {cur_lufs:.2f} → {ref_lufs:.2f} dB, gain {gain_db:.2f} dB")

    # write
    sf.write(out_path, to_mono(y_out), sr)  # write mono by default for simplicity
    progress_cb(100)
    log_cb(f"Escrito: {out_path}")