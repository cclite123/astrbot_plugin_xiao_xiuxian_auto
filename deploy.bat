@echo off
chcp 65001 >nul 2>&1
title Deploy - xiaoxiuxian

echo.
echo  ============================================
echo   xiaoxiuxian - Deploy Tool
echo  ============================================
echo.

REM Check if deploy-config.json exists
if exist "%~dp0deploy-config.json" (
    echo [INFO] Found deploy-config.json, deploying...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -Config "%~dp0deploy-config.json" %*
) else (
    echo [ERROR] deploy-config.json not found!
    echo.
    echo  Please create deploy-config.json first:
    echo.
    echo    1. Copy deploy-config.example.json to deploy-config.json
    echo    2. Edit deploy-config.json with your server info:
    echo.
    echo       {
    echo         "host":       "user@your-server-ip",
    echo         "port":       "22",
    echo         "key":        "C:\\path\\to\\your\\key.pem",
    echo         "remote_dir": "/opt/astrbot/data/plugins/astrbot_plugin_xiao_xiuxian_auto"
    echo       }
    echo.
    echo  Then double-click deploy.bat to deploy!
    echo.
    echo  Other options:
    echo    deploy.bat --dry-run    Preview only
    echo    deploy.bat --reload     Deploy and reload plugin
    echo.
    pause
    exit /b 1
)

echo.
pause
