from __future__ import annotations

import argparse
import unittest
from unittest.mock import Mock

from pathlib import Path
import tempfile

from run_stem_lab import (
    apply_demucs_runtime_args,
    apply_qa_mode,
    build_ytdlp_download_command,
    manifest_args_snapshot,
    supports_live_progress,
    ytdlp_failure_hint,
)


class ManifestArgsSnapshotTests(unittest.TestCase):
    def test_relinks_placeholder_slug_to_final_slug(self) -> None:
        args = argparse.Namespace(
            url="https://www.youtube.com/watch?v=abc123",
            project_slug="downloaded_track",
            skip_scoring=False,
        )

        snapshot = manifest_args_snapshot(args, "clean_title_slug")

        self.assertEqual(snapshot["project_slug"], "clean_title_slug")
        self.assertEqual(snapshot["requested_project_slug"], "downloaded_track")

    def test_preserves_matching_slug_without_extra_field(self) -> None:
        args = argparse.Namespace(
            url="https://www.youtube.com/watch?v=abc123",
            project_slug="clean_title_slug",
            skip_scoring=False,
        )

        snapshot = manifest_args_snapshot(args, "clean_title_slug")

        self.assertEqual(snapshot["project_slug"], "clean_title_slug")
        self.assertNotIn("requested_project_slug", snapshot)

    def test_supports_live_progress_requires_tty_output(self) -> None:
        fake_stream = Mock()
        fake_stream.isatty.return_value = False

        self.assertFalse(supports_live_progress(stream=fake_stream, is_terminal=True))

    def test_supports_live_progress_allows_real_terminal(self) -> None:
        fake_stream = Mock()
        fake_stream.isatty.return_value = True

        self.assertTrue(supports_live_progress(stream=fake_stream, is_terminal=True))

    def test_apply_qa_mode_enforces_fast_preset(self) -> None:
        args = argparse.Namespace(
            qa_mode=True,
            models=["htdemucs_ft"],
            shifts=2,
            overlap=0.5,
            sample_rate=48000,
            output_format="wav",
            skip_scoring=False,
            skip_audition_report=False,
            open_audition_report=True,
        )

        updated = apply_qa_mode(args)

        self.assertEqual(updated.models, ["htdemucs_6s"])
        self.assertEqual(updated.shifts, 1)
        self.assertEqual(updated.overlap, 0.25)
        self.assertEqual(updated.sample_rate, 44100)
        self.assertEqual(updated.output_format, "mp3")
        self.assertTrue(updated.skip_scoring)
        self.assertTrue(updated.skip_audition_report)
        self.assertFalse(updated.open_audition_report)

    def test_apply_demucs_runtime_args_appends_device_jobs_and_track(self) -> None:
        args = argparse.Namespace(demucs_device="cpu", demucs_jobs=4)
        cmd = ["python", "-m", "demucs", "-n", "htdemucs_6s"]

        updated = apply_demucs_runtime_args(cmd, args, Path(r"C:\audio\track.wav"))

        self.assertIn("-d", updated)
        self.assertIn("cpu", updated)
        self.assertIn("-j", updated)
        self.assertIn("4", updated)
        self.assertEqual(updated[-1], r"C:\audio\track.wav")

    def test_build_ytdlp_download_command_includes_retry_hardening(self) -> None:
        cmd = build_ytdlp_download_command(
            Path(r"C:\python.exe"),
            Path(r"C:\workspace\ultimate_stem_lab\projects\demo\source"),
            "https://www.youtube.com/watch?v=abc123",
        )

        self.assertIn("-f", cmd)
        self.assertIn("bestaudio/best", cmd)
        self.assertIn("--concurrent-fragments", cmd)
        self.assertIn("4", cmd)
        self.assertIn("--retries", cmd)
        self.assertIn("--fragment-retries", cmd)
        self.assertIn("--extractor-retries", cmd)
        self.assertIn("--file-access-retries", cmd)
        self.assertIn("--retry-sleep", cmd)

    def test_ytdlp_failure_hint_detects_missing_js_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "yt_dlp.log"
            log_path.write_text(
                "WARNING:  No supported JavaScript runtime could be found.\n",
                encoding="utf-8",
            )

            hint = ytdlp_failure_hint(log_path)

        assert hint is not None
        self.assertIn("JavaScript runtime", hint)


if __name__ == "__main__":
    unittest.main()
