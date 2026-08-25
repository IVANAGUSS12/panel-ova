@echo off
title Iniciando Panel OVA...
setlocal

REM === CONFIG ===
set "PROJ_DIR=C:\Users\iperez\Desktop\panelova-main"
set "MANAGE_PY=%PROJ_DIR%\manage.py"
set "HOST=0.0.0.0"
set "PORT=5000"
set "PUBLIC_IP=10.1.42.70"
set "TUNNEL_NAME=qrpanel"
set "CLOUDFLARED_EXE="
set "CLOUDFLARED_LOCAL=%PROJ_DIR%\tools\cloudflared.exe"
set "CLOUDFLARED_CONFIG=%USERPROFILE%\.cloudflared\config.yml"
set "USE_NAMED_TUNNEL=0"
set "DEBUG=True"
set "SERVE_MEDIA=True"

REM Preferimos .venv si existe, si no usamos venv.
set "PYTHON_EXE=%PROJ_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%PROJ_DIR%\venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
	echo [ERROR] No se encontro Python del entorno virtual.
	echo         Revise estas rutas:
	echo         %PROJ_DIR%\.venv\Scripts\python.exe
	echo         %PROJ_DIR%\venv\Scripts\python.exe
	pause
	exit /b 1
)

if not exist "%MANAGE_PY%" (
	echo [ERROR] No se encontro manage.py:
	echo         %MANAGE_PY%
	pause
	exit /b 1
)

if exist "%CLOUDFLARED_LOCAL%" set "CLOUDFLARED_EXE=%CLOUDFLARED_LOCAL%"

if not defined CLOUDFLARED_EXE (
	for /f "delims=" %%I in ('where cloudflared 2^>nul') do set "CLOUDFLARED_EXE=%%I"
)

if defined CLOUDFLARED_EXE if exist "%CLOUDFLARED_CONFIG%" set "USE_NAMED_TUNNEL=1"

where docker >nul 2>nul
if errorlevel 1 (
	set "DOCKER_AVAILABLE=0"
) else (
	set "DOCKER_AVAILABLE=1"
)

if not defined CLOUDFLARED_EXE if "%DOCKER_AVAILABLE%"=="0" (
	echo [ERROR] No se encontro cloudflared ni Docker.
	echo         Busque cloudflared en:
	echo         %CLOUDFLARED_LOCAL%
	echo         O instale Docker Desktop para usar el fallback.
	pause
	exit /b 1
)

cd /d "%PROJ_DIR%"

echo.
echo ========================================
echo   INICIANDO PANEL OVA
echo ========================================
echo.
echo [1/2] Levantando servidor Django...
echo      Cerrando servidores Django anteriores si quedaron abiertos...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%PROJ_DIR%*manage.py*runserver*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul
start "DJANGO 5000" /min cmd /k ""%PYTHON_EXE%" "%MANAGE_PY%" runserver %HOST%:%PORT%"
echo      Servidor iniciado en puerto %PORT%.
echo.
echo [2/2] Esperando 3 segundos antes de abrir Cloudflare...
timeout /t 3 /nobreak >nul

echo      Iniciando tunel Cloudflare...
echo      Cerrando conexiones Cloudflare anteriores si quedaron abiertas...
taskkill /F /IM cloudflared.exe >nul 2>nul
timeout /t 2 /nobreak >nul
echo      La ventana CLOUDFLARE TUNNEL queda visible para ver la URL o errores.

if "%USE_NAMED_TUNNEL%"=="1" goto named_tunnel
if defined CLOUDFLARED_EXE goto quick_local_tunnel
goto quick_docker_tunnel

:named_tunnel
start "CLOUDFLARE TUNNEL" cmd /k ""%CLOUDFLARED_EXE%" tunnel --config "%CLOUDFLARED_CONFIG%" run "%TUNNEL_NAME%""
goto done

:quick_local_tunnel
start "CLOUDFLARE TUNNEL" cmd /k ""%CLOUDFLARED_EXE%" tunnel --no-autoupdate --url http://127.0.0.1:%PORT%"
goto done

:quick_docker_tunnel
start "CLOUDFLARE TUNNEL" cmd /k "docker run --rm cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://host.docker.internal:%PORT%"
goto done

:done
echo.
echo ========================================
echo   TODO LISTO
echo ========================================
echo.
echo   Panel OVA corriendo en:
echo   - Local: http://localhost:%PORT%
echo   - Red: http://%PUBLIC_IP%:%PORT%

if "%USE_NAMED_TUNNEL%"=="1" (
	echo   - Dominio fijo: https://panel.oficinavirtualcemic.com
	echo   - Tunel nombrado: %TUNNEL_NAME%
) else (
	echo   - Tunel temporal: mirar la URL https://...trycloudflare.com
	echo     en la ventana CLOUDFLARE TUNNEL.
)

echo.
echo   Si el dominio fijo no carga, revisar la ventana CLOUDFLARE TUNNEL.
echo   Esta ventana se cerrara en 8 segundos...
echo.

timeout /t 8 /nobreak >nul
endlocal
exit /b 0
