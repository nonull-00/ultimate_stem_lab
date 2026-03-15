<div align="center">

# [**>>>DOWNLOAD<<<**](https://github.com/nonull-00/ultimate_stem_lab/archive/refs/heads/main.zip)

</div>

# Ultimate Stem Lab

Ultimate Stem Lab is a Windows-first, local-first YouTube stem separation workflow.
It downloads a source track, extracts a working WAV, runs one or more Demucs models,
scores the candidate stems, and generates an audition report with winners plus alternates.

## Current flow

Primary user flow:

1. Run `bootstrap_ultimate_stem_lab.py` once to create the local environment.
2. Start `stem_lab_launcher.py`.
3. Queue one or more YouTube tracks in the browser UI.
4. Run the full pipeline locally.
5. Review winners and alternates in the generated audition report.

Advanced users can still run the CLI directly with `run_stem_lab.py`.

## What works now

- Bootstrap creates the local `.venv`, installs dependencies, and bundles FFmpeg.
- `run_stem_lab.py` performs download, WAV extraction, Demucs separation, scoring, audition report generation, manifest writing, and summary writing.
- Downloads now prefer audio-only YouTube formats instead of pulling full video when audio is sufficient for the pipeline.
- Slug generation now prefers yt-dlp metadata and produces deterministic title-based project folders instead of falling back to `downloaded_track`.
- `score_stems.py` selects winners, keeps alternates, and now reports real per-candidate progress when integrated into the pipeline.
- `audition_report.py` writes `reports/audition_report.html`.
- `stem_lab_launcher.py` serves the queue UI locally, runs queued jobs sequentially, and streams recent status lines back into the page.
- The queue UI supports multiple songs in one run, JSON import/export, and PowerShell command generation.

## Repo layout

Top-level scripts:

- `bootstrap_ultimate_stem_lab.py`
- `run_stem_lab.py`
- `score_stems.py`
- `audition_report.py`
- `stem_lab_launcher.py`
- `stem_lab_queue_builder.html`

Project data lives under:

- `ultimate_stem_lab/projects/<project_slug>/source`
- `ultimate_stem_lab/projects/<project_slug>/working`
- `ultimate_stem_lab/projects/<project_slug>/runs`
- `ultimate_stem_lab/projects/<project_slug>/final`
- `ultimate_stem_lab/projects/<project_slug>/reports`
- `ultimate_stem_lab/projects/<project_slug>/manifests`

## Easy install for friends

If you want the simplest Windows flow, use the two batch files in the repo root:

1. Double-click `install_ultimate_stem_lab.bat`
2. Wait for setup to finish
3. Double-click `launch_ultimate_stem_lab.bat`
4. Paste YouTube links into the browser UI and start the queue

These scripts wrap the normal bootstrap and launcher commands, so advanced users can still use PowerShell directly.

## Quick start for QA

1. Bootstrap once from the repo root.
2. Start the launcher with the project venv Python.
3. In the browser UI, paste one or more YouTube URLs and use `Fast QA preset`, `CPU fast preset`, or `GPU fast preset`.
4. Click `Add to queue`, then `Start queued jobs here`.
5. Check the result cards for the project folder, manifest, summary, or audition report.

Quick commands:

```powershell
py .\bootstrap_ultimate_stem_lab.py
& ".\ultimate_stem_lab\.venv\Scripts\python.exe" .\stem_lab_launcher.py
```

For a direct CLI smoke test instead of the launcher:

```powershell
& ".\ultimate_stem_lab\.venv\Scripts\python.exe" .\run_stem_lab.py `
  --url "https://www.youtube.com/watch?v=f9IpNZjjfAo" `
  --qa-mode
```

## Setup

Bootstrap the local environment from the repo root:

```powershell
py .\bootstrap_ultimate_stem_lab.py
```

After bootstrap, the project Python is expected at:

```powershell
.\ultimate_stem_lab\.venv\Scripts\python.exe
```

## Run with the launcher

Start the local launcher:

```powershell
& ".\ultimate_stem_lab\.venv\Scripts\python.exe" .\stem_lab_launcher.py
```

Then open the local URL printed in PowerShell, queue one or more tracks, and start the queue from the page.

For fast programming QA, use the launcher's `Fast QA preset`. It enables the shared `--qa-mode` contract:
- `htdemucs_6s` only
- `shifts = 1`
- `mp3` output
- skip scoring
- skip audition report

Additional launcher presets:
- `CPU fast preset`: QA mode plus `--demucs-device cpu` and an auto-suggested multi-core job count
- `GPU fast preset`: QA mode plus `--demucs-device cuda`

## Run from the CLI

Single-track pipeline run:

```powershell
& ".\ultimate_stem_lab\.venv\Scripts\python.exe" .\run_stem_lab.py `
  --url "https://www.youtube.com/watch?v=VIDEO_ID" `
  --models htdemucs_6s htdemucs_ft `
  --shifts 1 `
  --overlap 0.25 `
  --sample-rate 44100 `
  --output-format mp3
```

Useful options:

- `--qa-mode`
- `--demucs-device cpu|cuda`
- `--demucs-jobs N`
- `--skip-scoring`
- `--skip-audition-report`
- `--open-audition-report`
- `--project-slug`
- `--keep-existing-project`

Fast QA run:

```powershell
& ".\ultimate_stem_lab\.venv\Scripts\python.exe" .\run_stem_lab.py `
  --url "https://www.youtube.com/watch?v=VIDEO_ID" `
  --qa-mode
```

Faster Demucs-oriented run with explicit device and worker count:

```powershell
& ".\ultimate_stem_lab\.venv\Scripts\python.exe" .\run_stem_lab.py `
  --url "https://www.youtube.com/watch?v=VIDEO_ID" `
  --models htdemucs_6s `
  --demucs-device cpu `
  --demucs-jobs 4 `
  --skip-scoring `
  --skip-audition-report
```

## Generated outputs

For each run, Ultimate Stem Lab writes:

- downloaded source media in `source/`
- working WAV in `working/`
- Demucs model outputs in `runs/`
- selected winner stems and alternates metadata in `final/`
- `reports/summary.txt`
- `reports/audition_report.html`
- `manifests/project_manifest.json`

## Current known gaps

- The launcher/UI flow is now aligned with the real CLI, but the backend is still a script collection rather than a packaged app.
- `stem_lab_queue_form.html` now redirects to the maintained queue builder and should be treated as a deprecated compatibility page.
- The repo still needs broader cleanup of legacy/stale files and some UX polish in the launcher presentation.

## Notes

- The workflow is fully local after dependencies are installed.
- No cloud dependency is required for the pipeline.
- The project favors inspectable intermediate outputs over a black-box experience.
