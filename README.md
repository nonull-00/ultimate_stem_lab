<div align="center">

# [**>>>DOWNLOAD<<<**](https://github.com/nonull-00/ultimate_stem_lab/archive/refs/heads/main.zip)

</div>

# Ultimate Stem Lab

Ultimate Stem Lab is a Windows-first, local-first YouTube stem separation workflow.
It downloads a source track, extracts a working WAV, runs one or more Demucs models,
scores the candidate stems, and generates an audition report with winners plus alternates.

## Current flow

Primary user flow:

1. Download this repo and extract it somewhere on your Windows PC.
2. Double-click `install_ultimate_stem_lab.bat` once to set up Ultimate Stem Lab.
3. Double-click `launch_ultimate_stem_lab.bat` whenever you want to use it.
4. Queue one or more YouTube tracks in the browser UI.
5. Run the full pipeline locally.
6. Review winners and alternates in the generated audition report.

## What works now

- The installer creates the local `.venv`, installs dependencies, and bundles FFmpeg.
- `run_stem_lab.py` performs download, WAV extraction, Demucs separation, scoring, audition report generation, manifest writing, and summary writing.
- Downloads now prefer audio-only YouTube formats instead of pulling full video when audio is sufficient for the pipeline.
- Slug generation now prefers yt-dlp metadata and produces deterministic title-based project folders instead of falling back to `downloaded_track`.
- `score_stems.py` selects winners, keeps alternates, and now reports real per-candidate progress when integrated into the pipeline.
- `audition_report.py` writes `reports/audition_report.html`.
- `stem_lab_launcher.py` serves the queue UI locally, runs queued jobs sequentially, and streams recent status lines back into the page.
- The queue UI supports multiple songs in one run and JSON import/export.

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

## Install and run

Ultimate Stem Lab is meant to be installed and launched from the two batch files in the repo root.

1. Make sure Python 3 is installed on Windows.
2. Download this repo, then extract the ZIP if Windows downloaded it as an archive.
3. Open the extracted folder.
4. Double-click `install_ultimate_stem_lab.bat`.
5. Wait for the installer window to finish. It creates the local environment and installs the required tools.
6. After install completes, double-click `launch_ultimate_stem_lab.bat`.
7. Keep the launcher window open and use the browser page at `http://127.0.0.1:8765`.

If Windows warns you before opening a batch file, choose the option to continue if you trust the repo copy you downloaded.

If you update the repo later, run `install_ultimate_stem_lab.bat` again before launching so the local environment stays in sync.

## Quick start

1. Double-click `launch_ultimate_stem_lab.bat`.
2. In the browser UI, paste one or more YouTube URLs.
3. Choose `Fast QA preset`, `CPU fast preset`, or `GPU fast preset`.
4. Click `Add to queue`, then `Start queued jobs here`.
5. Check the result cards for the project folder, manifest, summary, or audition report.

For fast programming QA, use the launcher's `Fast QA preset`. It enables the shared QA contract:
- `htdemucs_6s` only
- `shifts = 1`
- `mp3` output
- skip scoring
- skip audition report

Additional launcher presets:
- `CPU fast preset`: QA mode plus `--demucs-device cpu` and an auto-suggested multi-core job count
- `GPU fast preset`: QA mode plus `--demucs-device cuda`

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

- The launcher/UI flow is working well, but the backend is still a script collection rather than a packaged app.
- `stem_lab_queue_form.html` now redirects to the maintained queue builder and should be treated as a deprecated compatibility page.
- The repo still needs broader cleanup of legacy/stale files and some UX polish in the launcher presentation.

## Notes

- The workflow is fully local after dependencies are installed.
- No cloud dependency is required for the pipeline.
- The project favors inspectable intermediate outputs over a black-box experience.
