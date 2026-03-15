from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

from stem_lab_launcher import (
    AppState,
    Job,
    QueueRunner,
    ThreadingHTTPServer,
    extract_result_paths,
    make_handler,
)


class LauncherContractTests(unittest.TestCase):
    def test_extract_result_paths_reads_pipeline_success_lines(self) -> None:
        outputs = extract_result_paths(
            [
                "[+] Project directory: C:\\workspace\\ultimate_stem_lab\\projects\\demo",
                "[+] Manifest: C:\\workspace\\ultimate_stem_lab\\projects\\demo\\manifests\\project_manifest.json",
                "[+] Summary: C:\\workspace\\ultimate_stem_lab\\projects\\demo\\reports\\summary.txt",
                "[+] Audition report written: C:\\workspace\\ultimate_stem_lab\\projects\\demo\\reports\\audition_report.html",
            ]
        )

        self.assertEqual(outputs["project_dir"], r"C:\workspace\ultimate_stem_lab\projects\demo")
        self.assertTrue(outputs["manifest"].endswith(r"project_manifest.json"))
        self.assertTrue(outputs["summary"].endswith(r"summary.txt"))
        self.assertTrue(outputs["audition_report"].endswith(r"audition_report.html"))

    def test_extract_result_paths_joins_wrapped_windows_paths(self) -> None:
        outputs = extract_result_paths(
            [
                "[+] Project directory: C:\\workspace\\ultimate_stem_lab\\projects\\sounds",
                "_for_the_supermarket_1\\demo",
                "[+] Manifest: C:\\workspace\\ultimate_stem_lab\\projects\\sounds",
                "_for_the_supermarket_1\\demo\\manifests\\project_manifest.json",
                "[+] Summary: C:\\workspace\\ultimate_stem_lab\\projects\\sounds",
                "_for_the_supermarket_1\\demo\\reports\\summary.txt",
            ]
        )

        self.assertEqual(
            outputs["project_dir"],
            r"C:\workspace\ultimate_stem_lab\projects\sounds_for_the_supermarket_1\demo",
        )
        self.assertTrue(outputs["manifest"].endswith(r"demo\manifests\project_manifest.json"))
        self.assertTrue(outputs["summary"].endswith(r"demo\reports\summary.txt"))

    def test_extract_result_paths_reads_failure_stage_and_log(self) -> None:
        outputs = extract_result_paths(
            [
                "[!] Failure stage: download",
                "[!] Failure log: C:\\workspace\\ultimate_stem_lab\\projects\\demo\\logs\\yt_dlp.log",
            ]
        )

        self.assertEqual(outputs["failure_stage"], "download")
        self.assertTrue(outputs["failure_log"].endswith(r"logs\yt_dlp.log"))

    def test_from_payload_ignores_unknown_fields(self) -> None:
        job = Job.from_payload(
            {
                "id": "job-1",
                "url": "https://www.youtube.com/watch?v=abc123",
                "pythonPath": r".\ultimate_stem_lab\.venv\Scripts\python.exe",
                "scriptPath": r".\run_stem_lab.py",
                "models": ["htdemucs_6s", "htdemucs_ft"],
                "projectRoot": r".\ultimate_stem_lab\projects",
                "unexpected": "value",
            }
        )

        self.assertEqual(job.id, "job-1")
        self.assertEqual(job.models, ["htdemucs_6s", "htdemucs_ft"])

    def test_build_command_emits_supported_flags_only(self) -> None:
        job = Job(
            id="job-2",
            url="https://youtu.be/abc123?si=test",
            pythonPath=r".\ultimate_stem_lab\.venv\Scripts\python.exe",
            scriptPath=r".\run_stem_lab.py",
            models=["htdemucs_6s", "htdemucs_ft"],
            demucsDevice="cpu",
            demucsJobs="4",
            scoreMode="skip",
            generateAudition=False,
            openAudition=True,
            audioBitrate="320k",
            keepSource=True,
            overwriteProject=True,
            useRichProgress=True,
        )

        cmd = job.build_command(Path(r"C:\workspace"))
        joined = " ".join(cmd)

        self.assertIn("--skip-scoring", cmd)
        self.assertIn("--skip-audition-report", cmd)
        self.assertIn("--demucs-device", cmd)
        self.assertIn("cpu", cmd)
        self.assertIn("--demucs-jobs", cmd)
        self.assertIn("4", cmd)
        self.assertNotIn("--mp3-bitrate", joined)
        self.assertNotIn("--manual-audition-only", joined)
        self.assertNotIn("--generate-audition-report", joined)
        self.assertNotIn("--keep-source-fragments", joined)
        self.assertNotIn("--overwrite-project", joined)
        self.assertNotIn("--rich-progress", joined)

    def test_build_command_supports_open_audition_alias(self) -> None:
        job = Job(
            id="job-3",
            url="https://www.youtube.com/watch?v=abc123",
            pythonPath=r".\ultimate_stem_lab\.venv\Scripts\python.exe",
            scriptPath=r".\run_stem_lab.py",
            models=["htdemucs_6s"],
            generateAudition=True,
            openAudition=True,
        )

        cmd = job.build_command(Path(r"C:\workspace"))
        self.assertIn("--open-audition-report", cmd)
        self.assertNotIn("--skip-audition-report", cmd)

    def test_build_command_prefers_qa_mode_contract(self) -> None:
        job = Job(
            id="job-qa",
            url="https://www.youtube.com/watch?v=abc123",
            pythonPath=r".\ultimate_stem_lab\.venv\Scripts\python.exe",
            scriptPath=r".\run_stem_lab.py",
            models=["htdemucs_6s", "htdemucs_ft"],
            qaMode=True,
            scoreMode="skip",
            generateAudition=False,
            openAudition=True,
        )

        cmd = job.build_command(Path(r"C:\workspace"))

        self.assertIn("--qa-mode", cmd)
        self.assertNotIn("--skip-scoring", cmd)
        self.assertNotIn("--skip-audition-report", cmd)
        self.assertNotIn("--open-audition-report", cmd)

    def test_queue_runner_processes_multiple_jobs_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AppState(
                root_dir=Path(tmpdir),
                html_path=Path(tmpdir) / "index.html",
                host="127.0.0.1",
                port=8765,
            )
            runner = QueueRunner(state)
            payload = [
                {
                    "id": "job-1",
                    "url": "https://www.youtube.com/watch?v=abc123",
                    "pythonPath": r".\ultimate_stem_lab\.venv\Scripts\python.exe",
                    "scriptPath": r".\run_stem_lab.py",
                    "models": ["htdemucs_6s"],
                },
                {
                    "id": "job-2",
                    "url": "https://www.youtube.com/watch?v=def456",
                    "pythonPath": r".\ultimate_stem_lab\.venv\Scripts\python.exe",
                    "scriptPath": r".\run_stem_lab.py",
                    "models": ["htdemucs_ft"],
                    "generateAudition": False,
                },
            ]

            with patch.object(QueueRunner, "_run_one", side_effect=[0, 0]):
                runner.start(payload)
                assert state.worker_thread is not None
                state.worker_thread.join(timeout=5)

            self.assertFalse(state.running)
            self.assertEqual(state.current_phase, "done")
            self.assertEqual([item["job_id"] for item in state.completed], ["job-1", "job-2"])
            self.assertEqual(len(state.failed), 0)

    def test_queue_runner_records_stopped_job_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AppState(
                root_dir=Path(tmpdir),
                html_path=Path(tmpdir) / "index.html",
                host="127.0.0.1",
                port=8765,
            )
            runner = QueueRunner(state)
            payload = [
                {
                    "id": "job-stop",
                    "url": "https://www.youtube.com/watch?v=abc123",
                    "pythonPath": r".\ultimate_stem_lab\.venv\Scripts\python.exe",
                    "scriptPath": r".\run_stem_lab.py",
                    "models": ["htdemucs_6s"],
                }
            ]

            def stop_during_run(cmd: list[str]) -> int:
                state.stop_requested = True
                return 1

            with patch.object(QueueRunner, "_run_one", side_effect=stop_during_run):
                runner.start(payload)
                assert state.worker_thread is not None
                state.worker_thread.join(timeout=5)

            self.assertFalse(state.running)
            self.assertEqual(len(state.stopped), 1)
            self.assertEqual(len(state.failed), 0)
            self.assertEqual(state.stopped[0]["outputs"]["failure_stage"], "stopped by user")

    def test_api_run_queue_accepts_multi_job_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AppState(
                root_dir=Path(tmpdir),
                html_path=Path(tmpdir) / "index.html",
                host="127.0.0.1",
                port=0,
            )
            runner = QueueRunner(state)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, runner))
            host, port = server.server_address
            state.port = int(port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            payload = {
                "queue": [
                    {
                        "id": "job-1",
                        "url": "https://www.youtube.com/watch?v=abc123",
                        "pythonPath": r".\ultimate_stem_lab\.venv\Scripts\python.exe",
                        "scriptPath": r".\run_stem_lab.py",
                        "models": ["htdemucs_6s"],
                    },
                    {
                        "id": "job-2",
                        "url": "https://www.youtube.com/watch?v=def456",
                        "pythonPath": r".\ultimate_stem_lab\.venv\Scripts\python.exe",
                        "scriptPath": r".\run_stem_lab.py",
                        "models": ["htdemucs_ft"],
                    },
                ]
            }

            try:
                with patch.object(QueueRunner, "_run_one", side_effect=[0, 0]):
                    request = Request(
                        f"http://{host}:{port}/api/run_queue",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=5) as response:
                        body = json.loads(response.read().decode("utf-8"))

                    self.assertTrue(body["ok"])

                    assert state.worker_thread is not None
                    state.worker_thread.join(timeout=5)

                    with urlopen(f"http://{host}:{port}/api/status", timeout=5) as response:
                        status = json.loads(response.read().decode("utf-8"))

                self.assertFalse(status["running"])
                self.assertEqual(status["queue_length"], 2)
                self.assertEqual([item["job_id"] for item in status["completed"]], ["job-1", "job-2"])
                self.assertEqual(status["failed"], [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_api_open_path_accepts_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "reports" / "summary.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("ok", encoding="utf-8")

            state = AppState(
                root_dir=root,
                html_path=root / "index.html",
                host="127.0.0.1",
                port=0,
            )
            runner = QueueRunner(state)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, runner))
            host, port = server.server_address
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            try:
                with patch("stem_lab_launcher.open_local_path") as mocked_open:
                    with urlopen(
                        f"http://{host}:{port}/api/open_path?path={target.as_posix()}",
                        timeout=5,
                    ) as response:
                        body = json.loads(response.read().decode("utf-8"))

                self.assertTrue(body["ok"])
                mocked_open.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
