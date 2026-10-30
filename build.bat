@echo off
REM ==========================================================
REM  WITE2 Chinese patch - build and deploy
REM
REM  build.bat            build + assets + verify + deploy
REM  build.bat dll        build DLL only
REM  build.bat assets     generate translation table and font only
REM  build.bat check      run verification only
REM  build.bat deploy     deploy only
REM ==========================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=all"

REM ---------- locate MSVC ----------
set "VCVARS="
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "!VSWHERE!" (
    for /f "usebackq tokens=*" %%i in (`"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
        if exist "%%i\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=%%i\VC\Auxiliary\Build\vcvars32.bat"
    )
)
if not defined VCVARS if exist "D:\Software\VS\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=D:\Software\VS\VC\Auxiliary\Build\vcvars32.bat"
if not defined VCVARS if exist "%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat"

if not defined VCVARS (
    echo [ERROR] vcvars32.bat not found.
    echo         Install Visual Studio with "Desktop development with C++".
    exit /b 1
)

if /i "%TARGET%"=="dll"    goto only_dll
if /i "%TARGET%"=="assets" goto only_assets
if /i "%TARGET%"=="check"  goto only_check
if /i "%TARGET%"=="deploy" goto only_deploy

REM ---------- full pipeline ----------
call :do_dll
if errorlevel 1 goto fail
call :do_assets
if errorlevel 1 goto fail
call :do_check
call :do_deploy
goto done

:only_dll
call :do_dll
if errorlevel 1 goto fail
goto done

:only_assets
call :do_assets
if errorlevel 1 goto fail
goto done

:only_check
call :do_check
goto done

:only_deploy
call :do_deploy
if errorlevel 1 goto fail
goto done

REM ==========================================================
:do_dll
echo.
echo === Building version.dll ===
call "!VCVARS!" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] vcvars32.bat failed
    exit /b 1
)
if not exist build mkdir build
cl /nologo /LD /MT /O2 /W3 /GS- /D_CRT_SECURE_NO_WARNINGS /utf-8 ^
   src\hook_text.c ^
   /Fe:build\version.dll ^
   /Fo:build\hook.obj ^
   /link /DEF:src\version.def /MACHINE:X86 /SUBSYSTEM:WINDOWS
if errorlevel 1 (
    echo [FAILED] compile error
    exit /b 1
)
echo        build\version.dll
exit /b 0

:do_assets
echo.
echo === Generating translation table and font ===
python tools\build_assets.py
if errorlevel 1 exit /b 1
exit /b 0

:do_check
echo.
echo === Verifying ===
python tools\test_match.py
if errorlevel 1 (
    echo [FAILED] matching regression tests
    exit /b 1
)
python tools\build_assets.py --check
exit /b 0

:do_deploy
echo.
echo === Deploying to game directory ===
python tools\deploy.py
exit /b %errorlevel%

:fail
echo.
echo === FAILED ===
endlocal
exit /b 1

:done
echo.
echo === Done ===
if exist build\version.dll dir /b build\*.dll
endlocal
exit /b 0
