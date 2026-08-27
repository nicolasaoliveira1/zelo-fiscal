@echo off
REM Abre o painel do Zelo (painel.pyw): sobe o app, mostra o log e da acesso
REM as utilidades. Sem console: quem mostra a saida e o proprio painel.
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
    echo ERRO: venv nao encontrado. Crie com: python -m venv venv
    pause
    exit /b 1
)

start "" "venv\Scripts\pythonw.exe" "painel.pyw"
