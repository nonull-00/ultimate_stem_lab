#!/usr/bin/env python3
r"""
Ultimate Stem Lab - Tiny Local Launcher

What it does
------------
- Serves the queue-builder HTML on a local web server
- Accepts queue JSON from the browser
- Runs queued Stem Lab jobs sequentially
- Exposes live status and recent log lines over HTTP
- Uses only the Python standard library

Default URL
-----------
http://127.0.0.1:8765

Typical usage
-------------
py .\stem_lab_launcher.py

Optional usage
--------------
py .\stem_lab_launcher.py --host 127.0.0.1 --port 8765 ^
  --html .\stem_lab_queue_builder.html ^
  --python .\ultimate_stem_lab\.venv\Scripts\python.exe ^
  --script .\run_stem_lab.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass, field, fields as dataclass_fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_LOG_LINES = 400
SUPPORTED_SCORE_MODES = {"integrated", "skip"}
RESULT_LINE_PREFIXES = {
    "[+] Project directory: ": "project_dir",
    "[+] Manifest: ": "manifest",
    "[+] Summary: ": "summary",
    "[+] Audition report written: ": "audition_report",
    "[!] Failure stage: ": "failure_stage",
    "[!] Failure log: ": "failure_log",
    "[!] Failure hint: ": "failure_hint",
}


def now_ts() -> float:
    return time.time()


def safe_resolve(path_str: str, base: Path) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = (base / p).resolve()
    else:
        p = p.resolve()
    return p


def normalize_youtube_url(raw: str) -> str:
    from urllib.parse import parse_qs, urlparse

    raw = raw.strip()
    if not raw:
        return raw

    parsed = urlparse(raw)
    host = parsed.netloc.lower().replace("www.", "")
    video_id = ""

    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/shorts/"):
            parts = parsed.path.split("/")
            if len(parts) > 2:
                video_id = parts[2]
        elif parsed.path.startswith("/embed/"):
            parts = parsed.path.split("/")
            if len(parts) > 2:
                video_id = parts[2]

    if not video_id:
        return raw
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_result_paths(lines: list[str]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    pending_key: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if pending_key and not stripped.startswith("["):
            outputs[pending_key] = outputs.get(pending_key, "") + stripped
            continue

        pending_key = None
        for prefix, key in RESULT_LINE_PREFIXES.items():
            if stripped.startswith(prefix):
                outputs[key] = stripped[len(prefix):].strip()
                pending_key = key
                break
    return outputs


def is_path_within_root(path: Path, root_dir: Path) -> bool:
    try:
        path.resolve().relative_to(root_dir.resolve())
        return True
    except ValueError:
        return False


def open_local_path(raw_path: str, root_dir: Path) -> None:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (root_dir / path).resolve()
    else:
        path = path.resolve()

    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if not is_path_within_root(path, root_dir):
        raise RuntimeError(f"Refusing to open a path outside the workspace: {path}")

    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def run_preflight_command(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="replace",
        )
    except FileNotFoundError:
        return False, "missing"
    except Exception as exc:
        return False, str(exc)

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        return False, output or f"exit {result.returncode}"
    first_line = output.splitlines()[0] if output else "ok"
    return True, first_line


def collect_preflight_status(root_dir: Path, python_path: Path, script_path: Path) -> dict[str, Any]:
    ffmpeg_path = root_dir / "ultimate_stem_lab" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    checks: list[dict[str, str]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "ok" if ok else "fail",
                "detail": detail,
            }
        )

    add_check("python", python_path.exists(), str(python_path if python_path.exists() else "missing"))
    add_check("run script", script_path.exists(), str(script_path if script_path.exists() else "missing"))
    add_check("ffmpeg", ffmpeg_path.exists(), str(ffmpeg_path if ffmpeg_path.exists() else "missing"))

    if python_path.exists():
        ok, detail = run_preflight_command([str(python_path), "--version"], root_dir)
        add_check("python version", ok, detail)

        ok, detail = run_preflight_command([str(python_path), "-m", "yt_dlp", "--version"], root_dir)
        add_check("yt-dlp", ok, detail)

        ok, detail = run_preflight_command([str(python_path), "-m", "demucs", "--help"], root_dir)
        add_check("demucs", ok, detail)

        ok, detail = run_preflight_command(
            [
                str(python_path),
                "-c",
                "import torch,sys; sys.stdout.write('cuda' if torch.cuda.is_available() else 'cpu')",
            ],
            root_dir,
        )
        add_check("torch device", ok, detail)

    cuda_check = next((item for item in checks if item["name"] == "torch device"), None)
    if cuda_check and cuda_check["status"] == "ok" and cuda_check["detail"] == "cuda":
        gpu_advice = "CUDA-ready Torch detected. GPU fast preset should work if your NVIDIA stack stays available."
    else:
        gpu_advice = "This install is currently CPU-only. Use CPU fast preset unless you intentionally install CUDA-enabled Torch."

    return {
        "ok": all(item["status"] == "ok" for item in checks),
        "checks": checks,
        "gpu_advice": gpu_advice,
        "python_path": str(python_path),
        "script_path": str(script_path),
    }


@dataclass
class Job:
    id: str
    url: str
    pythonPath: str
    scriptPath: str
    models: list[str]
    shifts: str = "1"
    overlap: str = "0.25"
    sampleRate: str = "44100"
    outputFormat: str = "mp3"
    demucsDevice: str = ""
    demucsJobs: str = ""
    audioBitrate: str = "320k"
    scoreMode: str = "integrated"
    qaMode: bool = False
    generateAudition: bool = True
    openAudition: bool = False
    keepSource: bool = False
    overwriteProject: bool = False
    useRichProgress: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Job":
        allowed = {item.name for item in dataclass_fields(cls)}
        filtered = {key: value for key, value in payload.items() if key in allowed}
        return cls(**filtered)

    def build_command(self, base_dir: Path) -> list[str]:
        python_exe = safe_resolve(self.pythonPath, base_dir)
        script_py = safe_resolve(self.scriptPath, base_dir)

        if not self.url.strip():
            raise RuntimeError("Queue item is missing a YouTube URL.")
        if not self.models:
            raise RuntimeError("Queue item must include at least one Demucs model.")

        cmd = [
            str(python_exe),
            str(script_py),
            "--url",
            normalize_youtube_url(self.url),
            "--models",
            *self.models,
            "--shifts",
            str(self.shifts),
            "--overlap",
            str(self.overlap),
            "--sample-rate",
            str(self.sampleRate),
            "--output-format",
            str(self.outputFormat),
        ]

        if str(self.demucsDevice).strip():
            cmd.extend(["--demucs-device", str(self.demucsDevice).strip()])
        if str(self.demucsJobs).strip():
            cmd.extend(["--demucs-jobs", str(self.demucsJobs).strip()])

        if self.qaMode:
            cmd.append("--qa-mode")
            return cmd

        score_mode = str(self.scoreMode).strip().lower()
        if score_mode not in SUPPORTED_SCORE_MODES:
            score_mode = "integrated"

        if score_mode == "skip":
            cmd.append("--skip-scoring")

        if not self.generateAudition:
            cmd.append("--skip-audition-report")
        elif self.openAudition:
            cmd.append("--open-audition-report")

        return cmd


@dataclass
class AppState:
    root_dir: Path
    html_path: Path
    host: str
    port: int
    python_path: Path = field(default_factory=Path)
    script_path: Path = field(default_factory=Path)
    jobs: list[Job] = field(default_factory=list)
    current_index: int = -1
    current_job_id: str | None = None
    current_command: list[str] = field(default_factory=list)
    current_phase: str = "idle"
    current_returncode: int | None = None
    running: bool = False
    stop_requested: bool = False
    started_at: float | None = None
    updated_at: float | None = None
    process: subprocess.Popen[str] | None = None
    recent_logs: list[str] = field(default_factory=list)
    current_job_lines: list[str] = field(default_factory=list)
    current_outputs: dict[str, str] = field(default_factory=dict)
    completed: list[dict[str, Any]] = field(default_factory=list)
    stopped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    worker_thread: threading.Thread | None = None

    def append_log(self, line: str) -> None:
        line = line.rstrip("\n")
        with self.lock:
            self.recent_logs.append(line)
            if len(self.recent_logs) > MAX_LOG_LINES:
                self.recent_logs = self.recent_logs[-MAX_LOG_LINES:]
            self.updated_at = now_ts()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "stop_requested": self.stop_requested,
                "current_index": self.current_index,
                "queue_length": len(self.jobs),
                "current_job_id": self.current_job_id,
                "current_command": self.current_command,
                "current_phase": self.current_phase,
                "current_returncode": self.current_returncode,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "completed": self.completed,
                "stopped": self.stopped,
                "failed": self.failed,
                "recent_logs": self.recent_logs[-120:],
                "current_outputs": self.current_outputs,
                "host": self.host,
                "port": self.port,
                "html_path": str(self.html_path),
                "root_dir": str(self.root_dir),
                "python_path": str(self.python_path),
                "script_path": str(self.script_path),
            }


class QueueRunner:
    def __init__(self, state: AppState) -> None:
        self.state = state

    def start(self, jobs_payload: list[dict[str, Any]]) -> None:
        with self.state.lock:
            if self.state.running:
                raise RuntimeError("A queue is already running.")

            jobs = [Job.from_payload(item) for item in jobs_payload]
            self.state.jobs = jobs
            self.state.running = True
            self.state.stop_requested = False
            self.state.started_at = now_ts()
            self.state.updated_at = now_ts()
            self.state.current_phase = "queued"
            self.state.recent_logs = []
            self.state.current_job_lines = []
            self.state.current_outputs = {}
            self.state.completed = []
            self.state.stopped = []
            self.state.failed = []
            self.state.worker_thread = threading.Thread(target=self._run, daemon=True)
            self.state.worker_thread.start()

    def request_stop(self) -> None:
        with self.state.lock:
            self.state.stop_requested = True
            proc = self.state.process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            for index, job in enumerate(self.state.jobs):
                with self.state.lock:
                    if self.state.stop_requested:
                        self.state.current_phase = "stopped"
                        break
                    self.state.current_index = index
                    self.state.current_job_id = job.id
                    self.state.current_command = job.build_command(self.state.root_dir)
                    self.state.current_phase = "starting"
                    self.state.current_returncode = None
                    self.state.current_job_lines = []
                    self.state.current_outputs = {}
                    self.state.updated_at = now_ts()

                self.state.append_log("")
                self.state.append_log("=" * 80)
                self.state.append_log(f"JOB {index + 1}/{len(self.state.jobs)}")
                self.state.append_log(f"URL: {job.url}")
                self.state.append_log("COMMAND: " + " ".join(self.state.current_command))
                self.state.append_log("=" * 80)

                start_t = now_ts()
                return_code = self._run_one(self.state.current_command)
                elapsed = round(now_ts() - start_t, 2)

                result = {
                    "job_index": index,
                    "job_id": job.id,
                    "url": job.url,
                    "returncode": return_code,
                    "elapsed_sec": elapsed,
                    "command": self.state.current_command[:],
                    "outputs": dict(self.state.current_outputs),
                    "stopped_by_user": bool(self.state.stop_requested and return_code != 0),
                }

                if result["stopped_by_user"] and not result["outputs"].get("failure_stage"):
                    result["outputs"]["failure_stage"] = "stopped by user"

                with self.state.lock:
                    self.state.current_returncode = return_code
                    self.state.updated_at = now_ts()
                    if result["stopped_by_user"]:
                        self.state.stopped.append(result)
                        self.state.current_phase = "stopped_job"
                    elif return_code == 0:
                        self.state.completed.append(result)
                        self.state.current_phase = "completed_job"
                    else:
                        self.state.failed.append(result)
                        self.state.current_phase = "failed_job"

                self.state.append_log(
                    f"[{'SUCCESS' if return_code == 0 else 'FAILED'}] elapsed={elapsed:.2f}s returncode={return_code}"
                )

                if self.state.stop_requested:
                    with self.state.lock:
                        self.state.current_phase = "stopped"
                    break
        finally:
            with self.state.lock:
                if self.state.current_phase not in {"stopped"}:
                    self.state.current_phase = "done_with_failures" if self.state.failed else "done"
                self.state.running = False
                self.state.process = None
                self.state.updated_at = now_ts()

    def _run_one(self, cmd: list[str]) -> int:
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        proc = subprocess.Popen(
            cmd,
            cwd=str(self.state.root_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            creationflags=creationflags,
        )

        with self.state.lock:
            self.state.process = proc
            self.state.current_phase = "running"
            self.state.updated_at = now_ts()

        assert proc.stdout is not None
        for line in proc.stdout:
            clean_line = line.rstrip("\n")
            self.state.append_log(clean_line)
            with self.state.lock:
                self.state.current_job_lines.append(clean_line)
                self.state.current_outputs = extract_result_paths(self.state.current_job_lines)
            with self.state.lock:
                if self.state.stop_requested and proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass

        proc.wait()
        return int(proc.returncode or 0)


def make_handler(state: AppState, runner: QueueRunner):
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "UltimateStemLabLauncher/1.0"

        def _send_json(self, payload: Any, status: int = 200) -> None:
            raw = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, html_bytes: bytes, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html_bytes)

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(body.decode("utf-8"))

        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                html = state.html_path.read_bytes()
                self._send_html(html)
                return

            if self.path == "/api/status":
                self._send_json(state.snapshot())
                return

            if self.path == "/api/preflight":
                self._send_json(
                    collect_preflight_status(
                        state.root_dir,
                        state.python_path,
                        state.script_path,
                    )
                )
                return

            if self.path.startswith("/api/open_path"):
                parsed = urlparse(self.path)
                target = unquote(parsed.query.partition("path=")[2]).strip()
                if not target:
                    self._send_json({"ok": False, "error": "Missing path parameter."}, status=400)
                    return
                try:
                    open_local_path(target, state.root_dir)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, "opened": target})
                return

            if self.path == "/api/health":
                self._send_json({"ok": True, "status": state.snapshot()})
                return

            self._send_json({"ok": False, "error": "Not found"}, status=404)

        def do_POST(self) -> None:
            if self.path == "/api/normalize":
                payload = self._read_json()
                raw_url = str(payload.get("url", "")).strip()
                self._send_json({"ok": True, "normalized": normalize_youtube_url(raw_url)})
                return

            if self.path == "/api/run_queue":
                payload = self._read_json()
                queue_payload = payload.get("queue", [])
                if not isinstance(queue_payload, list) or not queue_payload:
                    self._send_json({"ok": False, "error": "Queue payload is empty."}, status=400)
                    return
                try:
                    runner.start(queue_payload)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, "message": "Queue started.", "status": state.snapshot()})
                return

            if self.path == "/api/stop":
                runner.request_stop()
                self._send_json({"ok": True, "message": "Stop requested.", "status": state.snapshot()})
                return

            self._send_json({"ok": False, "error": "Not found"}, status=404)

        def log_message(self, fmt: str, *args: Any) -> None:
            print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    return RequestHandler


def write_api_enabled_html(original_html: Path, target_html: Path) -> None:
    html = original_html.read_text(encoding="utf-8")

    inject = r"""
<script>
(function () {
  async function postJson(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return await res.json();
  }

  function appendStatusPanel() {
    if (document.getElementById("launcherStatusPanel")) return;

    const panel = document.createElement("section");
    panel.className = "panel stack";
    panel.id = "launcherStatusPanel";
    panel.innerHTML = `
      <div>
        <h2>Launcher status</h2>
        <p>Start the whole queue directly from this page and monitor current progress.</p>
      </div>
      <div class="toolbar">
        <button class="good" id="startQueueBtn">Start queued jobs here</button>
        <button class="danger" id="stopQueueBtn">Stop current run</button>
        <button id="refreshStatusBtn">Refresh status</button>
        <button id="refreshPreflightBtn">Refresh preflight</button>
      </div>
      <div>
        <label for="launcherPreflightOutput">Install / GPU preflight</label>
        <textarea id="launcherPreflightOutput" class="output mono" readonly></textarea>
      </div>
      <div>
        <label for="launcherStatusOutput">Live status / recent output</label>
        <textarea id="launcherStatusOutput" class="output mono" readonly></textarea>
      </div>
      <div>
        <label>Job results</label>
        <div id="launcherResultCards" class="queue-list"></div>
      </div>
    `;
    document.querySelector(".wrap").appendChild(panel);

    document.getElementById("startQueueBtn").onclick = async () => {
      try {
        const payload = { queue: (window.state && window.state.queue) ? window.state.queue : [] };
        const data = await postJson("/api/run_queue", payload);
        await refreshLauncherStatus();
        if (!data.ok) alert(data.error || "Failed to start queue.");
      } catch (err) {
        alert("Failed to start queue: " + err.message);
      }
    };

    document.getElementById("stopQueueBtn").onclick = async () => {
      try {
        await postJson("/api/stop", {});
        await refreshLauncherStatus();
      } catch (err) {
        alert("Failed to stop queue: " + err.message);
      }
    };

    document.getElementById("refreshStatusBtn").onclick = refreshLauncherStatus;
    document.getElementById("refreshPreflightBtn").onclick = refreshLauncherPreflight;
  }

  async function openPath(path) {
    const res = await fetch("/api/open_path?path=" + encodeURIComponent(path));
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Could not open path.");
  }

  async function copyPath(path) {
    await navigator.clipboard.writeText(path);
  }

  function friendlyFailureMessage(outputs) {
    const hint = outputs.failure_hint || "";
    const stage = outputs.failure_stage || "";
    if (hint.includes("Torch") && hint.includes("CUDA")) {
      return "GPU mode is enabled, but this install only supports CPU. Use CPU fast preset or set Demucs device to cpu.";
    }
    if (stage === "demucs preflight" && hint.includes("CUDA")) {
      return "GPU mode is enabled, but this install only supports CPU. Use CPU fast preset or set Demucs device to cpu.";
    }
    if (stage === "tool verification" && hint.includes("install_ultimate_stem_lab.bat")) {
      return "Install looks incomplete. Run install_ultimate_stem_lab.bat first, then relaunch the queue.";
    }
    return hint;
  }

  function renderResultCards(data) {
    const root = document.getElementById("launcherResultCards");
    if (!root) return;

    const jobs = [
      ...(Array.isArray(data.completed) ? data.completed : []),
      ...(Array.isArray(data.stopped) ? data.stopped : []),
      ...(Array.isArray(data.failed) ? data.failed : []),
    ].sort((a, b) => (Number(a.job_index ?? 9999) - Number(b.job_index ?? 9999)));
    root.innerHTML = "";

    if (!jobs.length) {
      root.innerHTML = '<div class="small">No completed jobs yet.</div>';
      return;
    }

    jobs.forEach((job, index) => {
      const outputs = job.outputs || {};
      const statusLabel = job.stopped_by_user ? "stopped" : (job.returncode === 0 ? "success" : "failed");
      const friendlyHint = friendlyFailureMessage(outputs);
      const card = document.createElement("div");
      card.className = "track-card";
      card.innerHTML = `
        <div class="track-top">
          <div>
            <div class="track-title">${index + 1}. ${job.url || ""}</div>
            <div class="small">status: ${statusLabel} | elapsed: ${job.elapsed_sec || 0}s</div>
          </div>
        </div>
        <div class="small mono">${(job.command || []).join(" ")}</div>
        <div class="small">stage: ${outputs.failure_stage || (job.returncode === 0 ? "completed" : "unknown failure")}</div>
        ${friendlyHint ? `<div class="small">${friendlyHint}</div>` : ""}
        <div class="small mono">${outputs.project_dir ? `project: ${outputs.project_dir}` : "project path not captured"}</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:4px;">
          ${outputs.project_dir ? '<button data-path-kind="project_dir">Open project</button><button data-copy-kind="project_dir">Copy project path</button>' : ""}
          ${outputs.manifest ? '<button data-path-kind="manifest">Open manifest</button>' : ""}
          ${outputs.summary ? '<button data-path-kind="summary">Open summary</button>' : ""}
          ${outputs.failure_log ? '<button data-path-kind="failure_log">Open log</button><button data-copy-kind="failure_log">Copy log path</button>' : ""}
          ${outputs.audition_report ? '<button data-path-kind="audition_report">Open audition report</button>' : ""}
        </div>
      `;

      card.querySelectorAll("button[data-path-kind]").forEach((button) => {
        button.onclick = async () => {
          const key = button.getAttribute("data-path-kind");
          if (!key || !outputs[key]) return;
          try {
            await openPath(outputs[key]);
          } catch (err) {
            alert("Open failed: " + err.message);
          }
        };
      });

      card.querySelectorAll("button[data-copy-kind]").forEach((button) => {
        button.onclick = async () => {
          const key = button.getAttribute("data-copy-kind");
          if (!key || !outputs[key]) return;
          try {
            await copyPath(outputs[key]);
          } catch (err) {
            alert("Copy failed: " + err.message);
          }
        };
      });

      root.appendChild(card);
    });
  }

  async function refreshLauncherPreflight() {
    const out = document.getElementById("launcherPreflightOutput");
    if (!out) return;

    try {
      const res = await fetch("/api/preflight");
      const data = await res.json();
      const lines = [];
      lines.push(`overall: ${data.ok ? "ready" : "needs attention"}`);
      lines.push(`python_path: ${data.python_path || ""}`);
      lines.push(`script_path: ${data.script_path || ""}`);
      lines.push("");
      (data.checks || []).forEach((check) => {
        lines.push(`${check.status}: ${check.name} -> ${check.detail}`);
      });
      if (data.gpu_advice) {
        lines.push("");
        lines.push(`gpu_advice: ${data.gpu_advice}`);
      }
      out.value = lines.join("\n");
    } catch (err) {
      out.value = "Preflight fetch failed: " + err.message;
    }
  }

  async function refreshLauncherStatus() {
    const out = document.getElementById("launcherStatusOutput");
    if (!out) return;

    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      const lines = [];
      lines.push(`running: ${data.running}`);
      lines.push(`phase: ${data.current_phase}`);
      lines.push(`queue: ${data.queue_length}`);
      lines.push(`current_index: ${data.current_index}`);
      lines.push(`current_job_id: ${data.current_job_id || ""}`);
      lines.push(`completed: ${Array.isArray(data.completed) ? data.completed.length : 0}`);
      lines.push(`stopped: ${Array.isArray(data.stopped) ? data.stopped.length : 0}`);
      lines.push(`failed: ${Array.isArray(data.failed) ? data.failed.length : 0}`);
      if (data.current_outputs && data.current_outputs.project_dir) {
        lines.push(`project_dir: ${data.current_outputs.project_dir}`);
      }
      lines.push("");
      lines.push("Recent output:");
      lines.push("-------------");
      (data.recent_logs || []).slice(-40).forEach(line => lines.push(line));
      out.value = lines.join("\n");
      out.scrollTop = out.scrollHeight;
      renderResultCards(data);
    } catch (err) {
      out.value = "Status fetch failed: " + err.message;
    }
  }

  window.addEventListener("load", () => {
    appendStatusPanel();
    refreshLauncherPreflight();
    refreshLauncherStatus();
    setInterval(refreshLauncherStatus, 2000);
  });
})();
</script>
"""

    if "</body>" in html:
        html = html.replace("</body>", inject + "\n</body>")
    else:
        html += inject

    target_html.write_text(html, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tiny local launcher for Ultimate Stem Lab.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--html", default="stem_lab_queue_builder.html", help="Path to the queue-builder HTML.")
    parser.add_argument("--python", dest="python_path", default=r".\ultimate_stem_lab\.venv\Scripts\python.exe")
    parser.add_argument("--script", dest="script_path", default=r".\run_stem_lab.py")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab automatically.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root_dir = Path.cwd().resolve()

    source_html = safe_resolve(args.html, root_dir)
    if not source_html.exists():
        print(f"[!] HTML not found: {source_html}")
        return 1

    served_html = root_dir / "_stem_lab_queue_builder_served.html"
    write_api_enabled_html(source_html, served_html)

    state = AppState(
        root_dir=root_dir,
        html_path=served_html,
        host=args.host,
        port=args.port,
        python_path=safe_resolve(args.python_path, root_dir),
        script_path=safe_resolve(args.script_path, root_dir),
    )

    runner = QueueRunner(state)
    handler_cls = make_handler(state, runner)
    server = ThreadingHTTPServer((args.host, args.port), handler_cls)

    url = f"http://{args.host}:{args.port}"
    print(f"[+] Ultimate Stem Lab launcher running at {url}")
    print(f"[+] Serving HTML: {served_html}")
    print("[+] Press Ctrl+C to stop.")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Shutting down launcher.")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
