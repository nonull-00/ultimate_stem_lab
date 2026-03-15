\
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
