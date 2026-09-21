@echo off
rem Abre o StudyIA a partir do codigo (modo desenvolvimento)
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
  start "" .venv\Scripts\pythonw.exe desktop.py
) else (
  start "" pythonw desktop.py
)
