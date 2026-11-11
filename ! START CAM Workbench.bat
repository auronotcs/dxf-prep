@echo off
setlocal
set "CAM_APP_DIR=%~dp0..\CAM Workbench"
if not exist "%CAM_APP_DIR%\Run CAM Workbench.bat" (
    echo Cannot find the CAM Workbench launcher in "%CAM_APP_DIR%".
    pause
    exit /b 1
)
pushd "%CAM_APP_DIR%"
if errorlevel 1 exit /b 1
call "Run CAM Workbench.bat"
set "CAM_LAUNCH_RESULT=%errorlevel%"
popd
exit /b %CAM_LAUNCH_RESULT%
