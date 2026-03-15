#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent / "ultimate_stem_lab"

SETTINGS_JSON = {
    "default_output_format": "mp3",
    "default_sample_rate": 44100,
    "default_models": ["htdemucs_6s", "htdemucs_ft"],
    "default_shifts": 1,
    "default_overlap": 0.25
}

REQUIREMENTS = """\
yt-dlp
demucs
audio-separator
numpy
scipy
soundfile
librosa
tqdm
rich
torchcodec
"""

README = r"""\
Ultimate Stem Lab

What this bootstrap does
- Creates the project folder structure
- Creates a Python virtual environment
- Installs Python dependencies
- Downloads and extracts FFmpeg
- Writes config/settings.json
- Writes requirements.txt
- Writes activate_env.bat

Important notes
- Run this file from the folder that contains it.
- After setup, use the venv Python to run run_stem_lab.py
- Demucs WAV output on newer torchaudio builds can require torchcodec.
  This bootstrap installs torchcodec automatically.
- FFmpeg is installed locally into:
  ultimate_stem_lab\tools\ffmpeg

Typical usage after setup
  .\ultimate_stem_lab\.venv\Scripts\python.exe .\run_stem_lab.py --url "https://youtu.be/NUs3s3nWXMI" --models htdemucs_ft --shifts 1 --output-format mp3
"""

ACTIVATE_BAT = r"""@echo off
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
if not exist "%VENV%\Scripts\activate.bat" (
  echo Virtual environment not found: %VENV%
  exit /b 1
)
call "%VENV%\Scripts\activate.bat"
set "PATH=%ROOT%tools\ffmpeg\bin;%PATH%"
echo Environment activated.
echo Python: %VENV%\Scripts\python.exe
"""

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    printable = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    log(f"[+] Running: {printable}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result


def download_file(url: str, dest: Path) -> None:
    log(f"[+] Downloading: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)
    log(f"[+] Saved: {dest}")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    log(f"[+] Wrote {path}")


def find_python_exe() -> Path:
    return Path(sys.executable).resolve()


def find_venv_python(project_root: Path) -> Path:
    return project_root / ".venv" / "Scripts" / "python.exe"


def ensure_structure(project_root: Path) -> None:
    log("[+] Creating folder structure")
    dirs = [
        project_root,
        project_root / "config",
        project_root / "tools",
        project_root / "projects",
        project_root / "models",
        project_root / "cache",
        project_root / "tmp",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def create_venv(project_root: Path, base_python: Path) -> Path:
    venv_python = find_venv_python(project_root)
    if venv_python.exists():
        log(f"[+] Virtual environment already exists: {venv_python}")
        return venv_python

    log("[+] Creating virtual environment")
    run([str(base_python), "-m", "venv", str(project_root / ".venv")], check=True)
    return venv_python


def install_python_packages(venv_python: Path, requirements_txt: Path) -> None:
    log("[+] Upgrading pip/setuptools/wheel")
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], check=True)

    log("[+] Installing project packages")
    run([str(venv_python), "-m", "pip", "install", "-r", str(requirements_txt)], check=True)


def install_ffmpeg(project_root: Path) -> Path:
    tools_dir = project_root / "tools"
    archive_path = tools_dir / "ffmpeg-release-essentials.zip"
    ffmpeg_root = tools_dir / "ffmpeg"

    if (ffmpeg_root / "bin" / "ffmpeg.exe").exists():
        log(f"[+] FFmpeg already installed at: {ffmpeg_root}")
        return ffmpeg_root

    download_file(FFMPEG_URL, archive_path)

    log("[+] Extracting FFmpeg")
    with zipfile.ZipFile(archive_path, "r") as zf:
        extract_base = tools_dir / "_ffmpeg_extract"
        if extract_base.exists():
            shutil.rmtree(extract_base)
        extract_base.mkdir(parents=True, exist_ok=True)
        zf.extractall(extract_base)

    extracted_dirs = [p for p in extract_base.iterdir() if p.is_dir()]
    if not extracted_dirs:
        raise RuntimeError("Could not find extracted FFmpeg directory.")

    source_dir = extracted_dirs[0]
    if ffmpeg_root.exists():
        shutil.rmtree(ffmpeg_root)
    shutil.move(str(source_dir), str(ffmpeg_root))
    shutil.rmtree(extract_base, ignore_errors=True)

    log(f"[+] Installed FFmpeg to: {ffmpeg_root}")
    return ffmpeg_root


def verify_install(venv_python: Path, ffmpeg_root: Path) -> None:
    ffmpeg_exe = ffmpeg_root / "bin" / "ffmpeg.exe"

    run([str(venv_python), "--version"], check=True)
    log(f"[+] Python OK: {subprocess.check_output([str(venv_python), '--version'], text=True).strip()}")

    ffmpeg_first = subprocess.check_output([str(ffmpeg_exe), "-version"], text=True, errors="replace").splitlines()[0]
    log(f"[+] FFmpeg OK: {ffmpeg_first}")

    ytdlp_version = subprocess.check_output([str(venv_python), "-m", "yt_dlp", "--version"], text=True).strip()
    log(f"[+] yt-dlp OK: {ytdlp_version}")

    demucs_help = subprocess.check_output(
        [str(venv_python), "-m", "demucs", "--help"],
        text=True,
        errors="replace",
    ).splitlines()[0]
    log(f"[+] Demucs OK: {demucs_help}")

    torchcodec_check = subprocess.check_output(
        [str(venv_python), "-c", "import importlib.util; print('ok' if importlib.util.find_spec('torchcodec') else 'missing')"],
        text=True,
        errors="replace",
    ).strip()
    if torchcodec_check == "ok":
        log("[+] TorchCodec package installed")
    else:
        log("[!] TorchCodec package not detected")

    rich_check = subprocess.check_output(
        [str(venv_python), "-c", "import rich; print('rich import ok')"],
        text=True,
        errors="replace",
    ).strip()
    log(f"[+] rich OK: {rich_check}")

    audio_separator_check = subprocess.check_output(
        [str(venv_python), "-c", "import audio_separator; print('audio-separator import ok')"],
        text=True,
        errors="replace",
    ).strip()
    log(f"[+] audio-separator OK: {audio_separator_check}")


def main() -> int:
    base_python = find_python_exe()

    ensure_structure(PROJECT_ROOT)

    requirements_path = PROJECT_ROOT / "requirements.txt"
    write_text(requirements_path, REQUIREMENTS)

    settings_path = PROJECT_ROOT / "config" / "settings.json"
    write_text(settings_path, json.dumps(SETTINGS_JSON, indent=2))

    activate_bat_path = PROJECT_ROOT / "activate_env.bat"
    write_text(activate_bat_path, ACTIVATE_BAT)

    readme_path = PROJECT_ROOT / "README.txt"
    write_text(readme_path, README)

    venv_python = create_venv(PROJECT_ROOT, base_python)
    install_python_packages(venv_python, requirements_path)

    ffmpeg_root = install_ffmpeg(PROJECT_ROOT)

    verify_install(venv_python, ffmpeg_root)

    log("")
    log("Setup complete.")
    log(f"Project folder: {PROJECT_ROOT}")
    log(f"Activate env: {activate_bat_path}")
    log(f"Run tool with: {venv_python} .\\run_stem_lab.py --url \"https://youtu.be/NUs3s3nWXMI\" --models htdemucs_ft --shifts 1 --output-format mp3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


