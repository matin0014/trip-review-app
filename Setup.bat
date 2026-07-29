@echo off
REM Double-click THIS file to set up the Trip Review Tool.
REM
REM It runs Setup.ps1 for you. That is a separate file because Windows opens
REM .ps1 files in Notepad on a double-click (a deliberate security default), and
REM running one can also be refused by the machine's execution policy. This
REM wrapper sidesteps both, so there is nothing to right-click and nothing to
REM type.
REM
REM On SUCCESS it then deletes the three setup-only files (this one, Setup.ps1,
REM SHA256SUMS.txt), leaving a folder that holds just the app. On FAILURE it
REM keeps them, because the reviewer needs to run this again after re-copying.
REM
REM %~dp0 is this file's own folder, so it works no matter where it is run from.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1"

REM "if errorlevel 1" is true for any exit code >= 1, so this leaves everything
REM in place unless Setup.ps1 reported READY.
if errorlevel 1 exit /b 1

del "%~dp0SHA256SUMS.txt" 2>nul
del "%~dp0Setup.ps1" 2>nul

REM Deleting the running batch file needs the "(goto) 2>nul &" prefix: cmd keeps
REM reading this file by byte offset, so a plain "del %~f0" leaves it printing
REM "The batch file cannot be found." at the reviewer. The failed goto abandons
REM the batch context first, so nothing tries to read the file after it is gone.
(goto) 2>nul & del "%~f0"
