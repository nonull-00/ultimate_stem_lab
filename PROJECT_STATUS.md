# Ultimate Stem Lab - Project Status

## Current working state

- Bootstrap works and produces the local Windows environment.
- `run_stem_lab.py` now runs the full pipeline end to end.
- Scoring is integrated into the main pipeline.
- Audition report generation is integrated into the main pipeline.
- Title-based slug generation is working in real runs.
- The launcher now accepts queue payloads safely and builds commands that match the real CLI.
- The launcher-served queue builder supports multi-song queueing and direct queue execution.
- The pipeline now shows real scoring progress when `score_stems.py` is invoked from `run_stem_lab.py`.
- A shared `--qa-mode` contract now exists for fast local QA runs from both the CLI and launcher.
- Demucs performance knobs are now exposed for device selection and multi-core job counts.
- The launcher now distinguishes user-stopped jobs from genuine failures in its result state.

## Recently completed

- Replaced fragile `downloaded_track` fallback behavior with deterministic metadata-first slug selection.
- Fixed the manifest audition report path to match the actual report output location in `reports/`.
- Added cleanup for empty placeholder project scaffolding after a successful slug relink.
- Refreshed `README.md` to match the real repo and current user flow.
- Added a first-class fast QA preset that skips scoring and audition generation while keeping the split pipeline testable end to end.
- Added launcher-facing CPU fast and GPU fast presets on top of the shared QA path.

## Remaining priorities

1. Keep improving the launcher so it feels like the default everyday interface.
2. Tighten queue UX and expose more useful post-run actions in the launcher flow.
3. Continue modularizing shared pipeline logic out of the top-level scripts.
4. Clean up remaining legacy prototype files and stale contract assumptions.

## Notes

- `stem_lab_queue_builder.html` is the current launcher-served UI.
- `stem_lab_queue_form.html` is now a deprecated redirect page that points testers back to the maintained queue builder.
