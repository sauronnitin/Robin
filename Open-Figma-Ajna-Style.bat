@echo off
cd /d "%~dp0"
uv run python docs/case-study-jobhunter/_gen_figma_ajna_style_plugin.py
echo.
echo In Figma: Plugins ^> Development ^> Import plugin from manifest
echo   docs\case-study-jobhunter\figma-ajna-style-plugin\manifest.json
echo Then run: 1. Build design system  2. Style all JobHunter slides
pause
