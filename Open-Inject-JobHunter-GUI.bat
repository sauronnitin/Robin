@echo off
setlocal
cd /d "%~dp0docs\case-study-jobhunter"
echo.
echo JobHunter Figma GUI injector (embedded PNGs)
echo ============================================
python _gen_figma_plugin_code.py
if errorlevel 1 exit /b 1
echo.
echo 1) In Figma: Plugins ^> Development ^> Import plugin from manifest...
echo    docs\case-study-jobhunter\figma-inject-plugin\manifest.json
echo 2) Run: JobHunter GUI Inject ^> Inject all JobHunter GUI screens
echo.
pause
