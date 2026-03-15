#!/usr/bin/env python3
r'''
score_stems.py

Analyze separated stem candidates from Ultimate Stem Lab project runs, score them
using stem-specific heuristics, and copy the winning stem for each instrument into
<project>/final.

Example:
    python score_stems.py --project ".\ultimate_stem_lab\projects\downloaded_track"

Optional:
    python score_stems.py --project ".\ultimate_stem_lab\projects\downloaded_track" \
        --source ".\ultimate_stem_lab\projects\downloaded_track\working\song.wav" \
        --report-name stem_selection_report.txt

Notes:
- Supports wav/mp3/flac/ogg/m4a/aac/webm input stems via librosa.
- Uses weighted rule-based scoring rather than a model, so it is transparent,
  fast, and easy to tune.
- Cross-stem leakage is estimated from correlations against an internally built
  vocal/drum/bass reference per run.
'''

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table
except Exception:  # pragma: no cover
    Console = None
    Progress = None
    Panel = None
    Table = None

try:
    import librosa
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "librosa is required. Install it in your Ultimate Stem Lab venv first."
    ) from exc


console = Console() if Console else None

SUPPORTED_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".webm"}
DEFAULT_SR = 44100
EPS = 1e-9


STEM_PRIORITY = ["vocals", "drums", "bass", "other", "guitar", "piano"]


WEIGHTS: Dict[str, Dict[str, float]] = {
    "vocals": {
        "vocal_focus": 0.22,
        "midrange_focus": 0.15,
        "harmonic_ratio": 0.12,
        "stereo_width": 0.06,
        "dynamic_health": 0.05,
        "low_drum_bleed": 0.15,
        "low_bass_bleed": 0.08,
        "low_artifact_penalty": 0.12,
        "low_hf_noise": 0.05,
    },
    "drums": {
        "transient_strength": 0.24,
        "percussive_ratio": 0.18,
        "dynamic_health": 0.08,
        "stereo_width": 0.05,
        "low_vocal_bleed": 0.16,
        "low_harmonic_smear": 0.12,
        "low_artifact_penalty": 0.12,
        "low_hf_noise": 0.05,
    },
    "bass": {
        "bass_focus": 0.28,
        "low_end_stability": 0.20,
        "harmonic_ratio": 0.08,
        "dynamic_health": 0.06,
        "low_vocal_bleed": 0.10,
        "low_drum_bleed": 0.10,
        "low_artifact_penalty": 0.12,
        "low_hf_noise": 0.06,
    },
    "other": {
        "broadband_balance": 0.18,
        "harmonic_ratio": 0.14,
        "stereo_width": 0.08,
        "dynamic_health": 0.08,
        "low_vocal_bleed": 0.18,
        "low_drum_bleed": 0.12,
        "low_bass_bleed": 0.08,
        "low_artifact_penalty": 0.10,
        "low_hf_noise": 0.04,
    },
    "guitar": {
        "harmonic_ratio": 0.22,
        "midrange_focus": 0.15,
        "stereo_width": 0.08,
        "dynamic_health": 0.08,
        "low_vocal_bleed": 0.14,
        "low_drum_bleed": 0.10,
        "low_artifact_penalty": 0.15,
        "low_hf_noise": 0.08,
    },
    "piano": {
        "harmonic_ratio": 0.22,
        "midrange_focus": 0.12,
        "stereo_width": 0.08,
        "dynamic_health": 0.08,
        "transient_strength": 0.08,
        "low_vocal_bleed": 0.12,
        "low_drum_bleed": 0.10,
        "low_artifact_penalty": 0.13,
        "low_hf_noise": 0.07,
    },
}


@dataclass
class Candidate:
    stem_type: str
    model_name: str
    run_dir: str
    audio_path: str
    raw_metrics: Dict[str, float]
    features: Dict[str, float]
    normalized: Dict[str, float]
    score: float
    winner: bool = False


@dataclass
class RunBundle:
    model_name: str
    stem_paths: Dict[str, Path]
    references: Dict[str, np.ndarray]


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score stem candidates and choose winners.")
    parser.add_argument("--project", required=True, help="Path to an Ultimate Stem Lab project directory.")
    parser.add_argument(
        "--source",
        default=None,
        help="Optional source mix file. Defaults to <project>/working/*.wav if found.",
    )
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SR, help="Analysis sample rate.")
    parser.add_argument(
        "--final-dir-name",
        default="final",
        help="Name of final output folder inside project. Default: final",
    )
    parser.add_argument(
        "--json-name",
        default="stem_scores.json",
        help="Output JSON file name inside final dir.",
    )
    parser.add_argument(
        "--report-name",
        default="stem_selection_report.txt",
        help="Output report TXT file name inside final dir.",
    )
    parser.add_argument(
        "--copy-format",
        choices=["preserve", "wav", "mp3"],
        default="preserve",
        help="Final file extension style. 'preserve' keeps original extension.",
    )
    parser.add_argument(
        "--stem-order",
        nargs="*",
        default=STEM_PRIORITY,
        help="Preferred stem order for reporting.",
    )
    parser.add_argument(
        "--plain-log",
        action="store_true",
        help="Disable Rich progress UI and print plain log lines instead.",
    )
    parser.add_argument(
        "--keep-alternates",
        type=int,
        default=1,
        help="How many non-winning alternates to record per stem in JSON/report. Default: 1",
    )
    return parser.parse_args()


def ensure_project(project: Path) -> None:
    if not project.exists() or not project.is_dir():
        raise SystemExit(f"Project directory not found: {project}")
    runs_dir = project / "runs"
    if not runs_dir.exists():
        raise SystemExit(f"Runs directory not found: {runs_dir}")


def find_default_source(project: Path) -> Optional[Path]:
    working = project / "working"
    if not working.exists():
        return None
    wavs = sorted(working.glob("*.wav"))
    return wavs[0] if wavs else None


def load_audio(path: Path, sr: int) -> Tuple[np.ndarray, int]:
    audio, loaded_sr = librosa.load(path.as_posix(), sr=sr, mono=False)
    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)
    return audio.astype(np.float32), loaded_sr


def mono_mix(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        return audio.astype(np.float32)
    return np.mean(audio, axis=0, dtype=np.float32)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + EPS))


def dbfs(x: np.ndarray) -> float:
    return 20.0 * math.log10(max(rms(x), EPS))


def peak_dbfs(x: np.ndarray) -> float:
    return 20.0 * math.log10(max(float(np.max(np.abs(x))), EPS))


def clip_ratio(x: np.ndarray, threshold: float = 0.999) -> float:
    return float(np.mean(np.abs(x) >= threshold))


def zero_cross_rate(x: np.ndarray) -> float:
    return float(np.mean(librosa.feature.zero_crossing_rate(y=x, frame_length=2048, hop_length=512)))


def spectral_centroid_mean(x: np.ndarray, sr: int) -> float:
    return float(np.mean(librosa.feature.spectral_centroid(y=x, sr=sr)))


def spectral_bandwidth_mean(x: np.ndarray, sr: int) -> float:
    return float(np.mean(librosa.feature.spectral_bandwidth(y=x, sr=sr)))


def spectral_rolloff_mean(x: np.ndarray, sr: int) -> float:
    return float(np.mean(librosa.feature.spectral_rolloff(y=x, sr=sr, roll_percent=0.85)))


def spectral_flatness_mean(x: np.ndarray) -> float:
    return float(np.mean(librosa.feature.spectral_flatness(y=np.maximum(x, -1.0))))


def hp_ratio(x: np.ndarray) -> Tuple[float, float]:
    harmonic, percussive = librosa.effects.hpss(x)
    h = np.sum(np.square(harmonic))
    p = np.sum(np.square(percussive))
    total = h + p + EPS
    return float(h / total), float(p / total)


def transient_strength(x: np.ndarray, sr: int) -> float:
    onset_env = librosa.onset.onset_strength(y=x, sr=sr)
    if onset_env.size == 0:
        return 0.0
    return float(np.percentile(onset_env, 95) / (np.mean(onset_env) + EPS))


def stereo_width(audio: np.ndarray) -> float:
    if audio.shape[0] < 2:
        return 0.0
    left = audio[0]
    right = audio[1]
    denom = np.sqrt(np.sum(left * left) * np.sum(right * right)) + EPS
    corr = float(np.sum(left * right) / denom)
    return float(np.clip((1.0 - corr) / 2.0, 0.0, 1.0))


def silence_ratio(x: np.ndarray, frame_length: int = 2048, hop_length: int = 512) -> float:
    frames = librosa.util.frame(x, frame_length=frame_length, hop_length=hop_length)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=0) + EPS)
    threshold = np.percentile(frame_rms, 20) * 0.5
    return float(np.mean(frame_rms <= max(threshold, 1e-5)))


def low_band_energy_ratio(x: np.ndarray, sr: int, low_hz: float, high_hz: float) -> float:
    s = np.abs(librosa.stft(x, n_fft=2048, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    total = float(np.sum(s)) + EPS
    mask = (freqs >= low_hz) & (freqs < high_hz)
    return float(np.sum(s[mask]) / total)


def hf_noise_ratio(x: np.ndarray, sr: int) -> float:
    return low_band_energy_ratio(x, sr, 8000.0, sr / 2.0)


def bass_focus_ratio(x: np.ndarray, sr: int) -> float:
    return low_band_energy_ratio(x, sr, 20.0, 180.0)


def midrange_focus_ratio(x: np.ndarray, sr: int) -> float:
    return low_band_energy_ratio(x, sr, 700.0, 3500.0)


def broadband_balance(x: np.ndarray, sr: int) -> float:
    low = low_band_energy_ratio(x, sr, 20.0, 250.0)
    mid = low_band_energy_ratio(x, sr, 250.0, 4000.0)
    high = low_band_energy_ratio(x, sr, 4000.0, min(12000.0, sr / 2.0))
    vals = np.array([low, mid, high], dtype=np.float64)
    target = np.array([1 / 3, 1 / 3, 1 / 3], dtype=np.float64)
    dist = float(np.linalg.norm(vals - target))
    return float(max(0.0, 1.0 - dist * 1.5))


def dynamic_health(crest_factor_db: float, clip: float) -> float:
    crest_norm = np.clip((crest_factor_db - 6.0) / 14.0, 0.0, 1.0)
    clip_penalty = np.clip(clip * 20.0, 0.0, 1.0)
    return float(np.clip(crest_norm * (1.0 - clip_penalty), 0.0, 1.0))


def artifact_penalty(flatness: float, zcr: float, hf_noise: float, clip: float) -> float:
    score = (
        np.clip(flatness / 0.35, 0.0, 1.0) * 0.35
        + np.clip(zcr / 0.18, 0.0, 1.0) * 0.20
        + np.clip(hf_noise / 0.25, 0.0, 1.0) * 0.25
        + np.clip(clip * 25.0, 0.0, 1.0) * 0.20
    )
    return float(np.clip(score, 0.0, 1.0))


def correlation_score(a: np.ndarray, b: np.ndarray) -> float:
    n = min(a.size, b.size)
    if n == 0:
        return 0.0
    a = a[:n]
    b = b[:n]
    a = a - np.mean(a)
    b = b - np.mean(b)
    denom = np.sqrt(np.sum(a * a) * np.sum(b * b)) + EPS
    return float(np.clip(np.sum(a * b) / denom, -1.0, 1.0))


def low_end_stability(x: np.ndarray, sr: int) -> float:
    s = np.abs(librosa.stft(x, n_fft=2048, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    mask = (freqs >= 20.0) & (freqs < 180.0)
    if not np.any(mask):
        return 0.0
    envelope = np.sum(s[mask], axis=0)
    if envelope.size == 0:
        return 0.0
    cv = float(np.std(envelope) / (np.mean(envelope) + EPS))
    return float(np.clip(1.0 - min(cv, 2.0) / 2.0, 0.0, 1.0))


def vocal_focus_proxy(mid_focus: float, harmonic_ratio_value: float, centroid: float) -> float:
    centroid_term = np.clip((centroid - 800.0) / 2200.0, 0.0, 1.0)
    return float(np.clip(0.45 * mid_focus + 0.35 * harmonic_ratio_value + 0.20 * centroid_term, 0.0, 1.0))


def harmonic_smear_penalty(harmonic_ratio_value: float, transient: float) -> float:
    return float(np.clip(0.7 * harmonic_ratio_value + 0.3 * (1.0 - np.clip((transient - 1.0) / 3.0, 0.0, 1.0)), 0.0, 1.0))


def scan_run_bundles(runs_dir: Path, sr: int) -> List[RunBundle]:
    bundles: List[RunBundle] = []
    for model_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        song_dirs = sorted(p for p in model_dir.iterdir() if p.is_dir())
        for song_dir in song_dirs:
            stem_paths: Dict[str, Path] = {}
            for file_path in sorted(song_dir.iterdir()):
                if file_path.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                stem_name = file_path.stem.lower()
                if stem_name in STEM_PRIORITY:
                    stem_paths[stem_name] = file_path
            if not stem_paths:
                continue

            references: Dict[str, np.ndarray] = {}
            for ref_name in ("vocals", "drums", "bass"):
                if ref_name in stem_paths:
                    audio, _ = load_audio(stem_paths[ref_name], sr)
                    references[ref_name] = mono_mix(audio)
            bundles.append(
                RunBundle(
                    model_name=model_dir.name,
                    stem_paths=stem_paths,
                    references=references,
                )
            )
    return bundles


def build_candidate(
    stem_type: str,
    path: Path,
    bundle: RunBundle,
    sr: int,
) -> Candidate:
    audio, _ = load_audio(path, sr)
    mono = mono_mix(audio)

    crest = peak_dbfs(mono) - dbfs(mono)
    flatness = spectral_flatness_mean(mono)
    zcr = zero_cross_rate(mono)
    harmonic_ratio_value, percussive_ratio_value = hp_ratio(mono)
    centroid = spectral_centroid_mean(mono, sr)
    bandwidth = spectral_bandwidth_mean(mono, sr)
    rolloff = spectral_rolloff_mean(mono, sr)
    transient = transient_strength(mono, sr)
    width = stereo_width(audio)
    silent = silence_ratio(mono)
    hf_noise = hf_noise_ratio(mono, sr)
    bass_focus = bass_focus_ratio(mono, sr)
    mid_focus = midrange_focus_ratio(mono, sr)
    clip = clip_ratio(mono)
    artifact = artifact_penalty(flatness, zcr, hf_noise, clip)
    dyn = dynamic_health(crest, clip)
    bass_stability = low_end_stability(mono, sr)
    vocal_focus = vocal_focus_proxy(mid_focus, harmonic_ratio_value, centroid)
    broad_balance = broadband_balance(mono, sr)
    smear = harmonic_smear_penalty(harmonic_ratio_value, transient)

    def bleed_against(ref_name: str) -> float:
        if ref_name not in bundle.references:
            return 0.0
        if stem_type == ref_name:
            return 0.0
        return max(0.0, correlation_score(mono, bundle.references[ref_name]))

    vocal_bleed = bleed_against("vocals")
    drum_bleed = bleed_against("drums")
    bass_bleed = bleed_against("bass")

    raw_metrics = {
        "duration_seconds": float(mono.size / sr),
        "peak_dbfs": peak_dbfs(mono),
        "rms_dbfs": dbfs(mono),
        "crest_factor_db": crest,
        "spectral_centroid_hz": centroid,
        "spectral_bandwidth_hz": bandwidth,
        "spectral_rolloff_hz": rolloff,
        "spectral_flatness": flatness,
        "zero_crossing_rate": zcr,
        "harmonic_ratio": harmonic_ratio_value,
        "percussive_ratio": percussive_ratio_value,
        "transient_strength": transient,
        "stereo_width": width,
        "silence_ratio": silent,
        "hf_noise_ratio": hf_noise,
        "bass_focus_ratio": bass_focus,
        "midrange_focus_ratio": mid_focus,
        "clip_ratio": clip,
        "artifact_penalty": artifact,
        "dynamic_health": dyn,
        "low_end_stability": bass_stability,
        "vocal_focus_proxy": vocal_focus,
        "broadband_balance": broad_balance,
        "harmonic_smear_penalty": smear,
        "vocal_bleed": vocal_bleed,
        "drum_bleed": drum_bleed,
        "bass_bleed": bass_bleed,
    }

    features = {
        "vocal_focus": vocal_focus,
        "midrange_focus": mid_focus,
        "harmonic_ratio": harmonic_ratio_value,
        "percussive_ratio": percussive_ratio_value,
        "transient_strength": np.clip((transient - 1.0) / 3.0, 0.0, 1.0),
        "stereo_width": width,
        "dynamic_health": dyn,
        "bass_focus": bass_focus,
        "low_end_stability": bass_stability,
        "broadband_balance": broad_balance,
        "low_vocal_bleed": float(1.0 - np.clip(vocal_bleed, 0.0, 1.0)),
        "low_drum_bleed": float(1.0 - np.clip(drum_bleed, 0.0, 1.0)),
        "low_bass_bleed": float(1.0 - np.clip(bass_bleed, 0.0, 1.0)),
        "low_artifact_penalty": float(1.0 - artifact),
        "low_hf_noise": float(1.0 - np.clip(hf_noise / 0.30, 0.0, 1.0)),
        "low_harmonic_smear": float(1.0 - smear),
    }

    return Candidate(
        stem_type=stem_type,
        model_name=bundle.model_name,
        run_dir=str(path.parent),
        audio_path=str(path),
        raw_metrics=raw_metrics,
        features=features,
        normalized={},
        score=0.0,
    )


def normalize_candidates(candidates: List[Candidate], stem_type: str) -> None:
    if not candidates:
        return
    keys = sorted({k for c in candidates for k in c.features})
    for key in keys:
        vals = np.array([float(c.features.get(key, 0.0)) for c in candidates], dtype=np.float64)
        min_v = float(np.min(vals))
        max_v = float(np.max(vals))
        if math.isclose(min_v, max_v, abs_tol=1e-12):
            norm_vals = np.full_like(vals, 0.5, dtype=np.float64)
        else:
            norm_vals = (vals - min_v) / (max_v - min_v)
        for cand, norm in zip(candidates, norm_vals):
            cand.normalized[key] = float(np.clip(norm, 0.0, 1.0))

    weights = WEIGHTS.get(stem_type, {})
    for cand in candidates:
        score = 0.0
        for feature_name, weight in weights.items():
            score += cand.normalized.get(feature_name, 0.5) * weight
        cand.score = float(score)


def choose_winners(grouped: Dict[str, List[Candidate]]) -> Dict[str, Candidate]:
    winners: Dict[str, Candidate] = {}
    for stem_type, cands in grouped.items():
        normalize_candidates(cands, stem_type)
        winner = max(cands, key=lambda c: (c.score, c.features.get("low_artifact_penalty", 0.0)))
        winner.winner = True
        winners[stem_type] = winner
    return winners


def safe_stem_filename(stem_type: str, source_path: Path, copy_format: str) -> str:
    if copy_format == "preserve":
        ext = source_path.suffix.lower() or ".wav"
    else:
        ext = f".{copy_format}"
    return f"{stem_type}_best{ext}"


def copy_winners(winners: Dict[str, Candidate], final_dir: Path, copy_format: str) -> Dict[str, str]:
    final_dir.mkdir(parents=True, exist_ok=True)
    copied: Dict[str, str] = {}
    for stem_type, winner in winners.items():
        src = Path(winner.audio_path)
        dst = final_dir / safe_stem_filename(stem_type, src, copy_format)
        shutil.copy2(src, dst)
        copied[stem_type] = str(dst)
    return copied


def detect_source_residual(source: Optional[Path], winners: Dict[str, Candidate], sr: int) -> Dict[str, float]:
    if source is None or not source.exists():
        return {}
    try:
        source_audio, _ = load_audio(source, sr)
    except Exception:
        return {}
    source_mono = mono_mix(source_audio)

    summed = np.zeros_like(source_mono)
    for winner in winners.values():
        try:
            audio, _ = load_audio(Path(winner.audio_path), sr)
        except Exception:
            continue
        mono = mono_mix(audio)
        n = min(summed.size, mono.size)
        summed[:n] += mono[:n]

    n = min(source_mono.size, summed.size)
    source_mono = source_mono[:n]
    summed = summed[:n]
    residual = source_mono - summed

    source_rms = rms(source_mono)
    residual_rms = rms(residual)
    return {
        "reconstruction_residual_rms": residual_rms,
        "reconstruction_residual_relative": float(residual_rms / (source_rms + EPS)),
        "reconstruction_residual_db": float(20.0 * math.log10((residual_rms + EPS) / (source_rms + EPS))),
    }


def write_json(
    candidates: Iterable[Candidate],
    winners: Dict[str, Candidate],
    copied_paths: Dict[str, str],
    project: Path,
    final_dir: Path,
    json_path: Path,
    reconstruction: Dict[str, float],
) -> None:
    payload = {
        "project": str(project),
        "final_dir": str(final_dir),
        "winners": {
            stem: {
                "model_name": cand.model_name,
                "audio_path": cand.audio_path,
                "score": cand.score,
                "copied_to": copied_paths.get(stem),
            }
            for stem, cand in winners.items()
        },
        "reconstruction": reconstruction,
        "candidates": [asdict(c) for c in candidates],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def top_reason(cand: Candidate, stem_type: str) -> List[str]:
    weights = WEIGHTS.get(stem_type, {})
    weighted = []
    for feat, weight in weights.items():
        weighted.append((cand.normalized.get(feat, 0.5) * weight, feat))
    weighted.sort(reverse=True)

    labels = {
        "vocal_focus": "better vocal focus",
        "midrange_focus": "stronger midrange clarity",
        "harmonic_ratio": "cleaner harmonic content",
        "percussive_ratio": "stronger percussive character",
        "transient_strength": "sharper transients",
        "stereo_width": "better stereo width",
        "dynamic_health": "healthier dynamics",
        "bass_focus": "stronger bass focus",
        "low_end_stability": "more stable low end",
        "broadband_balance": "better broadband balance",
        "low_vocal_bleed": "lower vocal bleed",
        "low_drum_bleed": "lower drum bleed",
        "low_bass_bleed": "lower bass bleed",
        "low_artifact_penalty": "fewer artifacts",
        "low_hf_noise": "less high-frequency hash",
        "low_harmonic_smear": "less harmonic smear",
    }
    reasons = [labels.get(feat, feat) for _, feat in weighted[:3]]
    return reasons


def write_report(
    grouped: Dict[str, List[Candidate]],
    winners: Dict[str, Candidate],
    copied_paths: Dict[str, str],
    source: Optional[Path],
    report_path: Path,
    reconstruction: Dict[str, float],
    stem_order: List[str],
) -> None:
    lines: List[str] = []
    lines.append("Stem Selection Report")
    lines.append("=" * 80)
    lines.append("")
    if source:
        lines.append(f"Source mix: {source}")
        lines.append("")
    if reconstruction:
        lines.append("Reconstruction check:")
        for key, value in reconstruction.items():
            lines.append(f"  {key}: {value:.6f}")
        lines.append("")

    ordered_stems = [s for s in stem_order if s in grouped] + [s for s in grouped if s not in stem_order]
    for stem_type in ordered_stems:
        lines.append(stem_type.upper())
        lines.append("-" * len(stem_type))
        winner = winners[stem_type]
        reasons = ", ".join(top_reason(winner, stem_type))
        lines.append(f"winner: {winner.model_name}")
        lines.append(f"score: {winner.score:.4f}")
        lines.append(f"source: {winner.audio_path}")
        lines.append(f"copied_to: {copied_paths.get(stem_type, '')}")
        lines.append(f"reason: {reasons}")
        lines.append("")
        lines.append("candidates:")
        ranked = sorted(grouped[stem_type], key=lambda c: c.score, reverse=True)
        for cand in ranked:
            flag = "WINNER" if cand.winner else ""
            lines.append(
                f"  - {cand.model_name:<18} score={cand.score:.4f} {flag}".rstrip()
            )
        lines.append("")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")



def choose_ranked(grouped: Dict[str, List[Candidate]]) -> tuple[Dict[str, Candidate], Dict[str, List[Candidate]]]:
    winners: Dict[str, Candidate] = {}
    ranked_groups: Dict[str, List[Candidate]] = {}
    for stem_type, cands in grouped.items():
        normalize_candidates(cands, stem_type)
        ranked = sorted(
            cands,
            key=lambda c: (c.score, c.features.get("low_artifact_penalty", 0.0)),
            reverse=True,
        )
        ranked[0].winner = True
        winners[stem_type] = ranked[0]
        ranked_groups[stem_type] = ranked
    return winners, ranked_groups


def write_json_v2(
    candidates: Iterable[Candidate],
    winners: Dict[str, Candidate],
    ranked_groups: Dict[str, List[Candidate]],
    copied_paths: Dict[str, str],
    project: Path,
    final_dir: Path,
    json_path: Path,
    reconstruction: Dict[str, float],
    keep_alternates: int,
) -> None:
    payload = {
        "project": str(project),
        "final_dir": str(final_dir),
        "winners": {},
        "reconstruction": reconstruction,
        "candidates": [asdict(c) for c in candidates],
    }
    for stem, cand in winners.items():
        ranked = ranked_groups.get(stem, [])
        alternates = []
        for alt in ranked[1 : 1 + max(0, keep_alternates)]:
            alternates.append(
                {
                    "model_name": alt.model_name,
                    "audio_path": alt.audio_path,
                    "score": alt.score,
                }
            )
        margin = cand.score - ranked[1].score if len(ranked) > 1 else cand.score
        payload["winners"][stem] = {
            "model_name": cand.model_name,
            "audio_path": cand.audio_path,
            "path": copied_paths.get(stem),
            "score": cand.score,
            "margin": margin,
            "notes": ", ".join(top_reason(cand, stem)),
            "alternates": alternates,
        }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def render_status_panel(lines: List[str]) -> "Panel | str":
    if console is None or Panel is None or Table is None:
        return "\n".join(lines[-8:])
    grid = Table.grid(expand=True)
    for line in lines[-8:]:
        grid.add_row(line)
    return Panel(grid, title="Stem Scoring", border_style="cyan")


def main() -> int:
    args = parse_args()
    project = Path(args.project).expanduser().resolve()
    ensure_project(project)

    source = Path(args.source).expanduser().resolve() if args.source else find_default_source(project)
    runs_dir = project / "runs"
    final_dir = project / args.final_dir_name
    json_path = final_dir / args.json_name
    report_path = final_dir / args.report_name

    log(f"[+] Project: {project}")
    if source:
        log(f"[+] Source mix: {source}")
    else:
        log("[!] No source mix found; reconstruction check will be skipped.")

    bundles = scan_run_bundles(runs_dir, args.sample_rate)
    if not bundles:
        raise SystemExit(f"No run folders with supported stem files found in: {runs_dir}")

    total_candidates = sum(len(bundle.stem_paths) for bundle in bundles)
    grouped: Dict[str, List[Candidate]] = {}
    all_candidates: List[Candidate] = []
    status_lines: List[str] = [f"Project: {project.name}", f"Candidates: {total_candidates}"]

    use_rich = (not args.plain_log) and console is not None and Progress is not None
    analysis_progress = None
    task_id = None

    if use_rich:
        analysis_progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        )
        analysis_progress.start()
        task_id = analysis_progress.add_task("Analyzing stems", total=max(total_candidates, 1))

    try:
        for bundle in bundles:
            for stem_type, path in bundle.stem_paths.items():
                message = f"{bundle.model_name} -> {stem_type} -> {path.name}"
                if use_rich:
                    status_lines.append(message)
                    console.print(render_status_panel(status_lines))
                else:
                    log(f"[progress] {len(all_candidates) + 1}/{total_candidates} {message}")
                cand = build_candidate(stem_type, path, bundle, args.sample_rate)
                grouped.setdefault(stem_type, []).append(cand)
                all_candidates.append(cand)
                if use_rich and analysis_progress is not None and task_id is not None:
                    analysis_progress.update(task_id, advance=1)
    finally:
        if use_rich and analysis_progress is not None:
            analysis_progress.stop()

    winners, ranked_groups = choose_ranked(grouped)
    copied_paths = copy_winners(winners, final_dir, args.copy_format)
    reconstruction = detect_source_residual(source, winners, args.sample_rate)

    write_json_v2(
        all_candidates,
        winners,
        ranked_groups,
        copied_paths,
        project,
        final_dir,
        json_path,
        reconstruction,
        args.keep_alternates,
    )
    write_report(grouped, winners, copied_paths, source, report_path, reconstruction, args.stem_order)

    # append alternates section to report
    with report_path.open("a", encoding="utf-8") as fh:
        fh.write("\nAlternates\n")
        fh.write("=" * 80 + "\n\n")
        ordered_stems = [s for s in args.stem_order if s in ranked_groups] + [s for s in ranked_groups if s not in args.stem_order]
        for stem in ordered_stems:
            fh.write(f"{stem}\n")
            fh.write("-" * len(stem) + "\n")
            ranked = ranked_groups[stem]
            if len(ranked) <= 1:
                fh.write("  no alternate available\n\n")
                continue
            for alt in ranked[1 : 1 + max(0, args.keep_alternates)]:
                fh.write(f"  - {alt.model_name}: score={alt.score:.4f} path={alt.audio_path}\n")
            fh.write("\n")

    log("")
    log("[+] Winners:")
    for stem in [s for s in args.stem_order if s in winners] + [s for s in winners if s not in args.stem_order]:
        winner = winners[stem]
        ranked = ranked_groups.get(stem, [])
        alt_txt = ""
        if len(ranked) > 1:
            alt = ranked[1]
            alt_txt = f"; alternate={alt.model_name} ({alt.score:.4f})"
        log(f"  - {stem}: {winner.model_name} -> {copied_paths[stem]} (score={winner.score:.4f}{alt_txt})")
    log(f"[+] JSON: {json_path}")
    log(f"[+] Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
