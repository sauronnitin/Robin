@echo off
cd /d "%~dp0docs\case-study-jobhunter"
python _build_figma_ajna_capture.py
echo.
echo Starting capture server on http://localhost:8778
echo Open figma-ajna-capture.html?s=1 through ?s=19 for Figma import
python -m http.server 8778
