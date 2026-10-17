@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Criando ambiente Python...
    python -m venv .venv || goto :erro
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :erro
)
start "" .venv\Scripts\pythonw.exe -m watransfer
exit /b 0
:erro
echo Falha ao preparar o ambiente. Verifique se o Python 3.11+ esta instalado.
pause
