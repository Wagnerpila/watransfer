@echo off
cd /d "%~dp0"

rem Instala (ou completa a instalacao) ate o marcador existir
if exist .venv\instalado.ok goto :abrir

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python nao encontrado. Instale o Python 3.11 ou superior em https://www.python.org/downloads/
    echo e marque a opcao "Add python.exe to PATH" durante a instalacao.
    pause
    exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo Criando ambiente Python...
    %PY% -m venv .venv || goto :erro
)
echo Instalando dependencias (somente na primeira vez, pode levar alguns minutos)...
.venv\Scripts\python.exe -m pip install --upgrade pip || goto :erro
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :erro
echo ok> .venv\instalado.ok

:abrir
start "" .venv\Scripts\pythonw.exe -m watransfer
exit /b 0

:erro
echo.
echo Falha ao preparar o ambiente. Verifique a conexao com a internet e rode este arquivo novamente.
pause
exit /b 1
