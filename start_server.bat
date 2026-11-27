@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
echo Open http://localhost:8080/radiator_support in your browser once the server has started.
python src/main.py
