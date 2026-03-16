#!/usr/bin/env python3
r"""
run_stem_lab.py

Ultimate Stem Lab runner for Windows-friendly workflows.

Features
- Downloads a source track with yt-dlp
- Extracts a working WAV with FFmpeg
- Runs one or more Demucs models
- Writes logs, manifest, and summary files
- Uses Rich terminal progress for clearer live status
- Defaults to MP3 output for a more reliable Windows workflow

Current practical default path
- output-format = mp3
- models = htdemucs_6s htdemucs_ft
- shifts = 1

Example
    .\ultimate_stem_lab\.venv\Scripts\python.exe .\run_stem_lab.py ^
        --url "https://youtu.be/NUs3s3nWXMI" ^
        --models htdemucs_6s htdemucs_ft ^
        --shifts 1 ^
        --output-format mp3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from ultimate_stem_lab.slug_utils import (
    choose_project_slug,
    load_ytdlp_metadata,
    safe_slug as slugify,
)

console = Console()


PROJECT_ROOT = Path(__file__).resolve().parent / "ultimate_stem_lab"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
DEFAULT_SETTINGS = {
    "default_output_format": "mp3",
    "default_sample_rate": 44100,
    "default_models": ["htdemucs_6s", "htdemucs_ft"],
    "default_shifts": 1,
    "default_overlap": 0.25,
}
QA_MODE_SETTINGS = {
    "models": ["htdemucs_6s"],
    "shifts": 1,
    "overlap": 0.25,
    "sample_rate": 44100,
    "output_format": "mp3",
}
YTDLP_RETRY_SETTINGS = {
    "retries": "10",
    "fragment_retries": "10",
    "extractor_retries": "3",
    "file_access_retries": "3",
    "retry_sleep": "1",
}

YTDLP_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
DEMUCS_PROGRESS_RE = re.compile(
    r"(\d+(?:\.\d+)?)%\|.*?\|\s*([0-9.]+)/([0-9.]+)"
)
SCORE_PROGRESS_RE = re.compile(r"^\[progress\]\s+(\d+)/(\d+)\s+(.*)$")


def supports_live_progress(stream: Any | None = None, is_terminal: bool | None = None) -> bool:
    output_stream = console.file if stream is None else stream
    terminal = console.is_terminal if is_terminal is None else is_terminal
    is_tty = getattr(output_stream, "isatty", lambda: False)()
    return bool(terminal and is_tty)


LIVE_PROGRESS_ENABLED = supports_live_progress()


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    start_time: str
    end_time: str
    duration_seconds: float
    log_file: str


@dataclass
class RunRecord:
    name: str
    model: str
    status: str
    duration_seconds: float
    output_dir: str
    log: str
    stems: dict[str, str]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_settings() -> dict:
    settings = DEFAULT_SETTINGS.copy()
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            settings.update({k: v for k, v in loaded.items() if v is not None})
        except Exception:
            pass
    return settings


def _legacy_safe_slug(text: str, fallback: str = "track") -> str:
    return slugify(text, fallback=fallback)
    text = text.strip()
    text = text.replace("&", "and")
    text = text.replace("'", "")
    text = text.replace("’", "")
    text = SAFE_SLUG_RE.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text or fallback


def project_dirs(base: Path, slug: str) -> dict[str, Path]:
    root = base / "projects" / slug
    return {
        "root": root,
        "source": root / "source",
        "working": root / "working",
        "runs": root / "runs",
        "logs": root / "logs",
        "reports": root / "reports",
        "manifests": root / "manifests",
        "thumbs": root / "thumbnails",
    }


def ensure_dirs(paths: Iterable[Path]) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def remove_empty_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.rmdir()
        except Exception:
            pass


def format_command(cmd: list[str]) -> str:
    return " ".join([f'"{x}"' if " " in x else x for x in cmd])


def default_python() -> Path:
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    return Path(sys.executable)


def ffmpeg_bin_dir() -> Path:
    return PROJECT_ROOT / "tools" / "ffmpeg" / "bin"


def ffmpeg_exe(name: str) -> Path:
    return ffmpeg_bin_dir() / f"{name}.exe"


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    ffbin = ffmpeg_bin_dir()
    env["PATH"] = str(ffbin) + os.pathsep + env.get("PATH", "")
    return env


def verify_tool(cmd: list[str], env: dict[str, str], label: str) -> None:
    console.print(f"[bold cyan][+][/bold cyan] Running: {format_command(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        raise RuntimeError(f"{label} check failed.\n{output}")
    first_line = ((result.stdout or "") + (result.stderr or "")).strip().splitlines()[0]
    console.print(f"[green][+][/green] {label} OK: {first_line}")


def probe_media(path: Path, env: dict[str, str]) -> dict:
    cmd = [
        str(ffmpeg_exe("ffprobe")),
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    console.print(f"[bold cyan][+][/bold cyan] Running: {format_command(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}\n{result.stderr}")
    return json.loads(result.stdout)


def find_downloaded_media(source_dir: Path) -> Path:
    candidates = []
    for ext in [".webm", ".m4a", ".mp4", ".mkv", ".mov", ".wav", ".mp3", ".flac", ".ogg"]:
        candidates.extend(source_dir.glob(f"*{ext}"))
    if not candidates:
        raise FileNotFoundError(f"No downloaded media found in {source_dir}")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def relink_downloaded_project_if_needed(
    requested_paths: dict[str, Path],
    source_file: Path,
) -> tuple[str, dict[str, Path], bool]:
    projects_dir = PROJECT_ROOT / "projects"
    existing_slugs = set()
    if projects_dir.exists():
        existing_slugs = {
            path.name
            for path in projects_dir.iterdir()
            if path.is_dir() and path.name != requested_paths["root"].name
        }

    metadata = load_ytdlp_metadata(requested_paths["source"])
    slug_selection = choose_project_slug(
        metadata=metadata,
        source_stem=source_file.stem,
        existing_slugs=existing_slugs,
        fallback="track",
    )
    improved_slug = slug_selection.final_slug

    if improved_slug == requested_paths["root"].name:
        return improved_slug, requested_paths, False

    improved_paths = project_dirs(PROJECT_ROOT, improved_slug)
    if improved_paths["root"].exists():
        return requested_paths["root"].name, requested_paths, False

    if improved_slug != slug_selection.base_slug:
        console.print(
            f"[yellow][!][/yellow] Project slug '{slug_selection.base_slug}' already exists. "
            f"Using '{improved_slug}' instead."
        )

    ensure_dirs(improved_paths.values())
    for item in requested_paths["source"].glob("*"):
        shutil.move(str(item), str(improved_paths["source"] / item.name))

    # move logs/thumbs/manifests/reports if any already exist
    for key in ["logs", "thumbs", "manifests", "reports", "working", "runs"]:
        if requested_paths[key].exists():
            for item in requested_paths[key].glob("*"):
                dest = improved_paths[key] / item.name
                if item.is_dir():
                    shutil.move(str(item), str(dest))
                else:
                    shutil.move(str(item), str(dest))

    remove_empty_dirs(
        requested_paths[key]
        for key in ["source", "working", "runs", "logs", "reports", "manifests", "thumbs"]
    )
    remove_empty_dirs([requested_paths["root"]])

    return improved_slug, improved_paths, True


def infer_stage_total_seconds(log_text: str) -> Optional[float]:
    # Demucs progress lines include current/total seconds.
    matches = DEMUCS_PROGRESS_RE.findall(log_text)
    if not matches:
        return None
    _, current_s, total_s = matches[-1]
    try:
        return float(total_s)
    except Exception:
        return None


def write_log_preamble(log_file: Path, cmd: list[str]) -> str:
    started = now_iso()
    with open(log_file, "w", encoding="utf-8", newline="") as f:
        f.write(f"COMMAND: {format_command(cmd)}\n")
        f.write(f"STARTED: {started}\n\n")
    return started


def append_log_end(log_file: Path, returncode: int) -> str:
    ended = now_iso()
    with open(log_file, "a", encoding="utf-8", newline="") as f:
        f.write(f"\nENDED: {ended}\n")
        f.write(f"EXIT_CODE: {returncode}\n")
    return ended


def render_dashboard(
    stage_progress: Progress,
    model_progress: Progress,
    status_lines: list[str],
) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_row(stage_progress)
    grid.add_row(model_progress)
    if status_lines:
        status_table = Table.grid(expand=True)
        for line in status_lines[-6:]:
            status_table.add_row(line)
        grid.add_row(status_table)
    return Panel(grid, title="Ultimate Stem Lab", border_style="cyan")


def run_command_with_progress(
    cmd: list[str],
    env: dict[str, str],
    log_file: Path,
    stage_progress: Progress,
    stage_task_id: TaskID,
    model_progress: Progress,
    model_label: str,
    mode: str = "generic",
) -> CommandResult:
    start_ts = time.time()
    started = write_log_preamble(log_file, cmd)

    task_total = None if mode == "generic" else 100.0
    task_id = model_progress.add_task(model_label, total=task_total, status="starting")
    console.print(f"[bold cyan][+][/bold cyan] Running: {format_command(cmd)}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
        env=env,
    )

    status_lines: list[str] = [f"[white]Model:[/white] {model_label}"]
    latest_percent: float = 0.0
    latest_detail: str = "running"

    if not LIVE_PROGRESS_ENABLED:
        assert process.stdout is not None
        with open(log_file, "a", encoding="utf-8", newline="") as log:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\n")
                log.write(raw_line)
                log.flush()

                if line.strip():
                    console.print(line)

                if mode == "demucs":
                    m = DEMUCS_PROGRESS_RE.search(line)
                    if m:
                        latest_percent = float(m.group(1))
                        current_s = float(m.group(2))
                        total_s = float(m.group(3))
                        latest_detail = f"{current_s:.2f}/{total_s:.2f}s"
                        model_progress.update(task_id, completed=latest_percent, total=100.0, status=latest_detail)
                elif mode == "ytdlp":
                    m = YTDLP_PROGRESS_RE.search(line)
                    if m:
                        latest_percent = float(m.group(1))
                        latest_detail = "downloading"
                        model_progress.update(task_id, completed=latest_percent, total=100.0, status=latest_detail)
                    else:
                        model_progress.update(task_id, status="preparing")
                elif mode == "scoring":
                    m = SCORE_PROGRESS_RE.search(line.strip())
                    if m:
                        current = int(m.group(1))
                        total = max(int(m.group(2)), 1)
                        latest_percent = (current / total) * 100.0
                        latest_detail = m.group(3).strip()
                        model_progress.update(task_id, completed=latest_percent, total=100.0, status=latest_detail)
                else:
                    model_progress.update(task_id, status=latest_detail)

        process.wait()
        rc = process.returncode

        if rc == 0:
            if mode == "generic":
                model_progress.update(task_id, status="done")
            else:
                model_progress.update(task_id, completed=100.0, total=100.0, status="done")
        else:
            if mode == "generic":
                model_progress.update(task_id, status=f"failed ({rc})")
            else:
                model_progress.update(task_id, completed=latest_percent, total=100.0, status=f"failed ({rc})")

        ended = append_log_end(log_file, rc)
        duration = time.time() - start_ts
        model_progress.remove_task(task_id)

        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)

        return CommandResult(
            command=cmd,
            returncode=rc,
            start_time=started,
            end_time=ended,
            duration_seconds=duration,
            log_file=str(log_file),
        )

    with Live(render_dashboard(stage_progress, model_progress, status_lines), console=console, refresh_per_second=10) as live:
        assert process.stdout is not None
        with open(log_file, "a", encoding="utf-8", newline="") as log:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\n")
                log.write(raw_line)
                log.flush()

                if line.strip():
                    status_lines.append(line.strip())

                if mode == "demucs":
                    m = DEMUCS_PROGRESS_RE.search(line)
                    if m:
                        percent = float(m.group(1))
                        current_s = float(m.group(2))
                        total_s = float(m.group(3))
                        latest_percent = percent
                        latest_detail = f"{current_s:.2f}/{total_s:.2f}s"
                        model_progress.update(
                            task_id,
                            completed=percent,
                            total=100.0,
                            status=latest_detail,
                        )
                elif mode == "ytdlp":
                    m = YTDLP_PROGRESS_RE.search(line)
                    if m:
                        percent = float(m.group(1))
                        latest_percent = percent
                        latest_detail = "downloading"
                        model_progress.update(
                            task_id,
                            completed=percent,
                            total=100.0,
                            status=latest_detail,
                        )
                    else:
                        model_progress.update(
                            task_id,
                            status="preparing",
                        )
                elif mode == "scoring":
                    m = SCORE_PROGRESS_RE.search(line.strip())
                    if m:
                        current = int(m.group(1))
                        total = max(int(m.group(2)), 1)
                        latest_percent = (current / total) * 100.0
                        latest_detail = m.group(3).strip()
                        model_progress.update(
                            task_id,
                            completed=latest_percent,
                            total=100.0,
                            status=latest_detail,
                        )
                else:
                    model_progress.update(task_id, status=latest_detail)

                live.update(render_dashboard(stage_progress, model_progress, status_lines))

        process.wait()
        rc = process.returncode

        if rc == 0:
            if mode == "generic":
                model_progress.update(task_id, status="done")
            else:
                model_progress.update(task_id, completed=100.0, total=100.0, status="done")
        else:
            if mode == "generic":
                model_progress.update(task_id, status=f"failed ({rc})")
            else:
                model_progress.update(task_id, completed=latest_percent, total=100.0, status=f"failed ({rc})")
        live.update(render_dashboard(stage_progress, model_progress, status_lines))

    ended = append_log_end(log_file, rc)
    duration = time.time() - start_ts
    model_progress.remove_task(task_id)

    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)

    return CommandResult(
        command=cmd,
        returncode=rc,
        start_time=started,
        end_time=ended,
        duration_seconds=duration,
        log_file=str(log_file),
    )


def extract_wav(
    ffmpeg_path: Path,
    input_file: Path,
    output_wav: Path,
    sample_rate: int,
    env: dict[str, str],
    stage_progress: Progress,
    stage_task_id: TaskID,
    model_progress: Progress,
) -> CommandResult:
    cmd = [
        str(ffmpeg_path),
        "-y",
        "-i", str(input_file),
        "-vn",
        "-ac", "2",
        "-ar", str(sample_rate),
        str(output_wav),
    ]
    log_file = output_wav.parent.parent / "logs" / "ffmpeg_extract.log"
    return run_command_with_progress(
        cmd,
        env,
        log_file,
        stage_progress,
        stage_task_id,
        model_progress,
        "ffmpeg extract",
        mode="generic",
    )


def collect_stems(output_dir: Path) -> dict[str, str]:
    stems: dict[str, str] = {}
    if not output_dir.exists():
        return stems
    for file in sorted(output_dir.iterdir()):
        if file.is_file() and file.suffix.lower() in {".mp3", ".wav", ".flac"}:
            stems[file.stem] = str(file)
    return stems


def output_switch_args(fmt: str) -> list[str]:
    fmt = fmt.lower()
    if fmt == "mp3":
        return ["--mp3"]
    if fmt == "flac":
        return ["--flac"]
    return []


def build_ytdlp_download_command(pyexe: Path, source_dir: Path, url: str) -> list[str]:
    return [
        str(pyexe),
        "-m", "yt_dlp",
        "--ffmpeg-location", str(ffmpeg_bin_dir()),
        "--no-playlist",
        "-f", "bestaudio/best",
        "--concurrent-fragments", "4",
        "--restrict-filenames",
        "--write-info-json",
        "--write-thumbnail",
        "--retries", YTDLP_RETRY_SETTINGS["retries"],
        "--fragment-retries", YTDLP_RETRY_SETTINGS["fragment_retries"],
        "--extractor-retries", YTDLP_RETRY_SETTINGS["extractor_retries"],
        "--file-access-retries", YTDLP_RETRY_SETTINGS["file_access_retries"],
        "--retry-sleep", YTDLP_RETRY_SETTINGS["retry_sleep"],
        "--output", str(source_dir / "%(title)s [%(id)s].%(ext)s"),
        url,
    ]


def apply_demucs_runtime_args(cmd: list[str], args: argparse.Namespace, track_path: Path) -> list[str]:
    if args.demucs_device:
        cmd.extend(["-d", str(args.demucs_device)])
    if args.demucs_jobs:
        cmd.extend(["-j", str(args.demucs_jobs)])
    cmd.append(str(track_path))
    return cmd


def emit_failure_context(stage: str, log_path: Path | None = None, hint: str | None = None) -> None:
    console.print(f"[red][!][/red] Failure stage: {stage}")
    if log_path is not None:
        console.print(f"[red][!][/red] Failure log: {log_path}")
    if hint:
        console.print(f"[yellow][!][/yellow] Failure hint: {hint}")


def ytdlp_failure_hint(log_path: Path) -> str | None:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    if "No supported JavaScript runtime could be found" in text:
        return "yt-dlp reported that no JavaScript runtime is installed; YouTube downloads may fail more often until a JS runtime is available."
    if "Incomplete YouTube ID" in text:
        return "The provided YouTube URL is incomplete or malformed."
    if "HTTP Error 403" in text or "Requested format is not available" in text:
        return "yt-dlp could not fetch a usable YouTube format for this URL. Retrying later or updating yt-dlp may help."
    return None


def demucs_failure_hint(log_path: Path, requested_device: str | None = None) -> str | None:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    lowered = text.lower()
    if "torch not compiled with cuda enabled" in lowered:
        return "CUDA was requested, but the installed PyTorch build does not support CUDA. Switch Demucs device to cpu or install a CUDA-enabled PyTorch build."
    if "found no nvidia driver" in lowered or "no cuda gpus are available" in lowered:
        return "CUDA was requested, but no usable NVIDIA CUDA device is available. Switch Demucs device to cpu or fix the GPU driver/runtime."
    if requested_device and requested_device.lower() == "cuda" and "cuda" in lowered and "error" in lowered:
        return "Demucs failed while using CUDA. Switching Demucs device to cpu is the fastest way to confirm whether this is a GPU setup issue."
    return None


def validate_demucs_device(pyexe: Path, env: dict[str, str], requested_device: str | None) -> str | None:
    if (requested_device or "").lower() != "cuda":
        return None

    cmd = [
        str(pyexe),
        "-c",
        "import torch,sys; sys.stdout.write('1' if torch.cuda.is_available() else '0')",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        return "CUDA was requested, but Torch could not be validated in the selected Python environment. Switch Demucs device to cpu or repair the Torch install."
    if result.stdout.strip() != "1":
        return "CUDA was requested, but the installed PyTorch build does not support CUDA on this machine. Switch Demucs device to cpu or install a CUDA-enabled PyTorch build."
    return None


def summary_text(
    project_slug: str,
    created_at: str,
    source_file: Path,
    working_wav: Path,
    models_requested: list[str],
    successful_runs: list[RunRecord],
    failed_runs: list[RunRecord],
) -> str:
    lines = []
    lines.append(f"Project: {project_slug}")
    lines.append(f"Created: {created_at}")
    lines.append(f"Source file: {source_file}")
    lines.append(f"Working WAV: {working_wav}")
    lines.append("")
    lines.append("Models requested:")
    for m in models_requested:
        lines.append(f"  - {m}")
    lines.append("")
    lines.append(f"Successful runs: {len(successful_runs)}")
    lines.append(f"Failed runs: {len(failed_runs)}")
    lines.append("")
    lines.append("Run details:")
    for run in successful_runs + failed_runs:
        lines.append(f"  - {run.name}: {run.status} ({run.duration_seconds:.2f}s)")
        lines.append(f"    output: {run.output_dir}")
        for stem_name, stem_path in run.stems.items():
            lines.append(f"      {stem_name}: {stem_path}")
        lines.append(f"    log: {run.log}")
    lines.append("")
    lines.append("Next recommended step:")
    lines.append("  Audition winner and alternate stems for confirmation, then keep the approved selections in final/.")
    return "\n".join(lines) + "\n"


def write_manifest(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def manifest_args_snapshot(args: argparse.Namespace, project_slug: str) -> dict[str, Any]:
    manifest_args = vars(args).copy()
    requested_slug = manifest_args.get("project_slug")
    if requested_slug != project_slug:
        manifest_args["requested_project_slug"] = requested_slug
        manifest_args["project_slug"] = project_slug
    return manifest_args


def apply_qa_mode(args: argparse.Namespace) -> argparse.Namespace:
    if not getattr(args, "qa_mode", False):
        return args

    args.models = list(QA_MODE_SETTINGS["models"])
    args.shifts = int(QA_MODE_SETTINGS["shifts"])
    args.overlap = float(QA_MODE_SETTINGS["overlap"])
    args.sample_rate = int(QA_MODE_SETTINGS["sample_rate"])
    args.output_format = str(QA_MODE_SETTINGS["output_format"])
    args.skip_scoring = True
    args.skip_audition_report = True
    args.open_audition_report = False
    return args


def parse_args(settings: dict) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Ultimate Stem Lab pipeline.")
    parser.add_argument("--url", required=True, help="Source URL to download with yt-dlp.")
    parser.add_argument("--models", nargs="+", default=settings.get("default_models", ["htdemucs_6s", "htdemucs_ft"]))
    parser.add_argument("--shifts", type=int, default=int(settings.get("default_shifts", 1)))
    parser.add_argument("--overlap", type=float, default=float(settings.get("default_overlap", 0.25)))
    parser.add_argument("--sample-rate", type=int, default=int(settings.get("default_sample_rate", 44100)))
    parser.add_argument("--output-format", choices=["mp3", "wav", "flac"], default=settings.get("default_output_format", "mp3"))
    parser.add_argument("--demucs-device", help="Demucs device override, e.g. cpu or cuda.")
    parser.add_argument("--demucs-jobs", type=int, help="Demucs job count for multi-core parallelism.")
    parser.add_argument(
        "--qa-mode",
        action="store_true",
        help="Use the fast QA preset: htdemucs_6s only, shifts 1, mp3 output, skip scoring, and skip audition report.",
    )
    parser.add_argument("--project-slug", default="downloaded_track", help="Initial project slug before title-based improvement.")
    parser.add_argument("--keep-existing-project", action="store_true", help="Do not warn or rename to title-derived slug.")
    parser.add_argument("--python-exe", default=str(default_python()), help="Python executable to use for subprocess tools.")
    parser.add_argument("--skip-scoring", action="store_true", help="Skip automatic post-run stem scoring.")
    parser.add_argument("--skip-audition-report", action="store_true", help="Skip automatic audition report generation.")
    parser.add_argument("--open-audition-report", action="store_true", help="Open the generated audition report in the default browser.")
    parser.add_argument("--score-script", default=str(Path(__file__).resolve().parent / "score_stems.py"), help="Path to score_stems.py")
    parser.add_argument("--audition-script", default=str(Path(__file__).resolve().parent / "audition_report.py"), help="Path to audition_report.py")
    return parser.parse_args()


def main() -> int:
    if not PROJECT_ROOT.exists():
        raise SystemExit(
            "Project folder not found. Run bootstrap_ultimate_stem_lab.py first."
        )

    settings = load_settings()
    args = parse_args(settings)
    args = apply_qa_mode(args)

    env = build_env()
    pyexe = Path(args.python_exe)
    ffmpeg_path = ffmpeg_exe("ffmpeg")

    if args.qa_mode:
        console.print("[yellow][!][/yellow] QA mode active: using the fast split preset and skipping downstream review stages.")

    # Verify tools explicitly.
    try:
        verify_tool([str(ffmpeg_path), "-version"], env, "ffmpeg")
        verify_tool([str(pyexe), "-m", "yt_dlp", "--version"], env, "yt-dlp")
        verify_tool([str(pyexe), "-m", "demucs", "--help"], env, "demucs")
    except FileNotFoundError:
        emit_failure_context(
            "tool verification",
            hint="A required local tool is missing. Run install_ultimate_stem_lab.bat or bootstrap_ultimate_stem_lab.py first.",
        )
        return 1
    except RuntimeError as exc:
        emit_failure_context("tool verification", hint=str(exc))
        return 1

    demucs_device_hint = validate_demucs_device(pyexe, env, args.demucs_device)
    if demucs_device_hint:
        emit_failure_context("demucs preflight", hint=demucs_device_hint)
        return 1

    created_at = now_iso()
    initial_paths = project_dirs(PROJECT_ROOT, slugify(args.project_slug, fallback="downloaded_track"))
    ensure_dirs(initial_paths.values())

    stage_progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
        disable=not LIVE_PROGRESS_ENABLED,
    )
    model_progress = Progress(
        SpinnerColumn(),
        TextColumn("[magenta]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn("{task.fields[status]}", justify="left"),
        console=console,
        transient=False,
        disable=not LIVE_PROGRESS_ENABLED,
    )

    stages = [
        "Download source",
        "Normalize project title",
        "Extract WAV",
        "Probe source",
        "Probe working WAV",
        "Run Demucs models",
        "Score stems",
        "Generate audition report",
        "Write manifest and summary",
    ]

    with stage_progress:
        stage_task = stage_progress.add_task("Pipeline", total=len(stages), current=0)

        # Stage 1: download
        source_dir = initial_paths["source"]
        ytdlp_log = initial_paths["logs"] / "yt_dlp.log"
        ytdlp_cmd = build_ytdlp_download_command(pyexe, source_dir, args.url)
        try:
            run_command_with_progress(
                ytdlp_cmd, env, ytdlp_log, stage_progress, stage_task, model_progress, "yt-dlp", mode="ytdlp"
            )
        except subprocess.CalledProcessError:
            emit_failure_context("download", ytdlp_log, ytdlp_failure_hint(ytdlp_log))
            raise
        source_file = find_downloaded_media(source_dir)
        stage_progress.advance(stage_task)

        # Stage 2: improve slug
        project_slug = initial_paths["root"].name
        project_paths = initial_paths
        if not args.keep_existing_project:
            project_slug, project_paths, moved = relink_downloaded_project_if_needed(initial_paths, source_file)
            if moved:
                source_file = find_downloaded_media(project_paths["source"])
        console.print(f"[green][+][/green] Project directory: {project_paths['root']}")
        stage_progress.advance(stage_task)

        # Working names
        source_stem_slug = slugify(source_file.stem, fallback="track")
        working_wav = project_paths["working"] / f"{source_stem_slug}.wav"

        # Stage 3: WAV extract
        extract_log = project_paths["logs"] / "ffmpeg_extract.log"
        try:
            extract_result = extract_wav(
                ffmpeg_path,
                source_file,
                working_wav,
                args.sample_rate,
                env,
                stage_progress,
                stage_task,
                model_progress,
            )
        except subprocess.CalledProcessError:
            emit_failure_context("extract", extract_log)
            raise
        stage_progress.advance(stage_task)

        # Stage 4 + 5: probing
        try:
            source_probe = probe_media(source_file, env)
        except Exception:
            emit_failure_context("probe source")
            raise
        stage_progress.advance(stage_task)
        try:
            working_probe = probe_media(working_wav, env)
        except Exception:
            emit_failure_context("probe working wav")
            raise
        stage_progress.advance(stage_task)

        # Stage 6: Demucs
        successful_runs: list[RunRecord] = []
        failed_runs: list[RunRecord] = []

        for model in args.models:
            run_name = f"demucs_{model}"
            log_file = project_paths["logs"] / f"{run_name}.log"
            model_cmd = [
                str(pyexe),
                "-m", "demucs",
                "-n", model,
                "--shifts", str(args.shifts),
                "--overlap", str(args.overlap),
                *output_switch_args(args.output_format),
                "-o", str(project_paths["runs"]),
            ]
            model_cmd = apply_demucs_runtime_args(model_cmd, args, working_wav)
            try:
                result = run_command_with_progress(
                    model_cmd,
                    env,
                    log_file,
                    stage_progress,
                    stage_task,
                    model_progress,
                    f"demucs {model}",
                    mode="demucs",
                )
                model_output_dir = project_paths["runs"] / model / working_wav.stem
                stems = collect_stems(model_output_dir)
                successful_runs.append(
                    RunRecord(
                        name=run_name,
                        model=model,
                        status="success",
                        duration_seconds=result.duration_seconds,
                        output_dir=str(model_output_dir),
                        log=str(log_file),
                        stems=stems,
                    )
                )
            except subprocess.CalledProcessError:
                hint = demucs_failure_hint(log_file, args.demucs_device)
                emit_failure_context(f"demucs {model}", log_file, hint)
                model_output_dir = project_paths["runs"] / model / working_wav.stem
                stems = collect_stems(model_output_dir)
                failed_runs.append(
                    RunRecord(
                        name=run_name,
                        model=model,
                        status="failed",
                        duration_seconds=0.0,
                        output_dir=str(model_output_dir),
                        log=str(log_file),
                        stems=stems,
                    )
                )
        stage_progress.advance(stage_task)

        if not successful_runs:
            console.print("[red][!][/red] No Demucs runs completed successfully.")
            return 1

        # Stage 7: scoring
        score_outputs = {}
        if args.skip_scoring:
            console.print("[yellow][!][/yellow] Skipping automatic scoring.")
        else:
            score_script = Path(args.score_script)
            if score_script.exists():
                score_log = project_paths["logs"] / "score_stems.log"
                score_cmd = [
                    str(pyexe),
                    str(score_script),
                    "--project", str(project_paths["root"]),
                    "--plain-log",
                    "--json-name", "stem_scores_v2.json",
                    "--report-name", "stem_selection_report_v2.txt",
                    "--keep-alternates", "1",
                ]
                try:
                    run_command_with_progress(
                        score_cmd,
                        env,
                        score_log,
                        stage_progress,
                        stage_task,
                        model_progress,
                        "score stems",
                        mode="scoring",
                    )
                    score_outputs = {
                        "json": str(project_paths["root"] / "final" / "stem_scores_v2.json"),
                        "report": str(project_paths["root"] / "final" / "stem_selection_report_v2.txt"),
                    }
                except subprocess.CalledProcessError:
                    emit_failure_context(
                        "score stems",
                        score_log,
                        hint="Scoring could not find usable stem outputs. This usually means Demucs failed or produced no supported stem files.",
                    )
                    return 1
            else:
                emit_failure_context("score stems", hint=f"Score script not found: {score_script}")
                return 1
        stage_progress.advance(stage_task)

        # Stage 8: audition report
        audition_report_path = ""
        if args.skip_audition_report:
            console.print("[yellow][!][/yellow] Skipping audition report generation.")
        else:
            audition_script = Path(args.audition_script)
            if audition_script.exists():
                audition_log = project_paths["logs"] / "audition_report.log"
                audition_cmd = [
                    str(pyexe),
                    str(audition_script),
                    "--project", str(project_paths["root"]),
                ]
                if args.open_audition_report:
                    audition_cmd.append("--open")
                try:
                    run_command_with_progress(
                        audition_cmd,
                        env,
                        audition_log,
                        stage_progress,
                        stage_task,
                        model_progress,
                        "audition report",
                        mode="generic",
                    )
                    audition_report_path = str(project_paths["reports"] / "audition_report.html")
                except subprocess.CalledProcessError:
                    emit_failure_context("audition report", audition_log)
                    return 1
            else:
                emit_failure_context("audition report", hint=f"Audition script not found: {audition_script}")
                return 1
        stage_progress.advance(stage_task)

        # Stage 9: summary + manifest
        manifest = {
            "project_slug": project_slug,
            "created_at": created_at,
            "args": manifest_args_snapshot(args, project_slug),
            "paths": {k: str(v) for k, v in project_paths.items()},
            "source_file": str(source_file),
            "working_wav": str(working_wav),
            "source_probe": source_probe,
            "working_probe": working_probe,
            "extract_result": asdict(extract_result),
            "successful_runs": [asdict(r) for r in successful_runs],
            "failed_runs": [asdict(r) for r in failed_runs],
            "score_outputs": score_outputs,
            "audition_report": audition_report_path,
        }
        manifest_path = project_paths["manifests"] / "project_manifest.json"
        write_manifest(manifest_path, manifest)

        summary = summary_text(
            project_slug,
            created_at,
            source_file,
            working_wav,
            args.models,
            successful_runs,
            failed_runs,
        )
        summary_path = project_paths["reports"] / "summary.txt"
        summary_path.write_text(summary, encoding="utf-8")
        stage_progress.advance(stage_task)

    console.print()
    console.print("[green][+][/green] Completed Ultimate Stem Lab run.")
    console.print(f"[green][+][/green] Project directory: {project_paths['root']}")
    console.print(f"[green][+][/green] Manifest: {manifest_path}")
    console.print(f"[green][+][/green] Summary: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
