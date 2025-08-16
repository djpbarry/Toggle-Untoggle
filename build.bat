@echo off
REM Activate your conda environment
call conda activate toggle-untoggle   REM <-- replace myenv with your environment name

REM Run PyInstaller
pyinstaller main.py --onedir --windowed --icon=icons/icon.png ^
--add-data "icons/icon.png;icons" ^
--add-data "icons/pen.png;icons" ^
--add-data "icons/eraser.png;icons" ^
--name=Toggle-Untoggle ^
--exclude-module PyQt6.QtMultimedia ^
--exclude-module PyQt6.QtWebEngineWidgets ^
--exclude-module PyQt6.QtMultimediaWidgets ^
--exclude-module PyQt6.QtNetwork ^
--exclude-module PyQt6.QtPositioning ^
--exclude-module PyQt6.QtLocation

