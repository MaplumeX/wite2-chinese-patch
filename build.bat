@echo off
REM ==========================================================
REM  Build WITE2 Chinese patch (32-bit proxy DLL)
REM  Output: build\version.dll
REM  Requires: Visual Studio 2019/2022 with C++ desktop workload
REM ==========================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "VCVARS="

REM --- locate vcvars32.bat via vswhere ---
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "!VSWHERE!" (
    for /f "usebackq tokens=*" %%i in (`"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
        if exist "%%i\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=%%i\VC\Auxiliary\Build\vcvars32.bat"
    )
)

REM --- fallback: common paths ---
if not defined VCVARS if exist "%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat"
if not defined VCVARS if exist "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat"

if not defined VCVARS (
    echo [ERROR] vcvars32.bat not found.
    echo         Install Visual Studio with "Desktop development with C++".
    exit /b 1
)
echo Using: !VCVARS!
call "!VCVARS!" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] failed to initialize MSVC environment
    exit /b 1
)

if not exist build mkdir build

echo.
echo === Building version.dll ===
cl /nologo /LD /MT /O2 /W3 /GS- /D_CRT_SECURE_NO_WARNINGS /utf-8 ^
   src\hook_text.c ^
   /Fe:build\version.dll ^
   /Fo:build\ ^
   /link /DEF:src\version.def /MACHINE:X86 /SUBSYSTEM:WINDOWS
if errorlevel 1 (
    echo.
    echo [FAILED]
    exit /b 1
)

echo.
echo === Building diagnostic tool (optional) ===
cl /nologo /LD /MT /O2 /W3 /GS- /D_CRT_SECURE_NO_WARNINGS /utf-8 ^
   tools\dump_code.c ^
   /Fe:build\version_diag.dll ^
   /Fo:build\diag_ ^
   /link /DEF:src\version.def /MACHINE:X86 /SUBSYSTEM:WINDOWS >nul 2>&1
if errorlevel 1 (
    echo [SKIP] diagnostic tool failed to build ^(not required^)
) else (
    echo        build\version_diag.dll
)

echo.
echo === Done ===
dir /b build\*.dll
endlocal
