@echo off
rem Build swap_panel.pyw into dist\LocalModelPanel\LocalModelPanel.exe
rem First install: python -m pip install -r requirements.txt pyinstaller
cd /d "%~dp0"
python -m PyInstaller --noconfirm --windowed --onedir --name LocalModelPanel ^
  --icon "%~dp0assets\icon.ico" --add-data "%~dp0assets;assets" ^
  --distpath "%~dp0dist" --workpath "%~dp0build" --specpath "%~dp0build" ^
  --exclude-module tkinter ^
  "%~dp0swap_panel.pyw"
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo Done: dist\LocalModelPanel\LocalModelPanel.exe
pause
