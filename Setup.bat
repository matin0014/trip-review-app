@echo off
REM Double-click THIS file to set up the Trip Review Tool.
REM
REM It just runs Setup.ps1 for you. That is a separate file because Windows
REM opens .ps1 files in Notepad on a double-click (a deliberate security
REM default), and running one can also be refused by the machine's execution
REM policy. This wrapper sidesteps both, so there is nothing to right-click,
REM no menu to find, and no command to type.
REM
REM %~dp0 is this file's own folder, so it works no matter where it is run from.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1"
