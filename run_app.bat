@echo off
REM Runs the app with Python instead of the .exe (needs Python 3.10+ from python.org).
cd /d "%~dp0"
python -m pip install --quiet -r requirements.txt
python app.py
