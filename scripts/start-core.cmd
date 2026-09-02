@echo off
REM Start the trading core and its local API. Point Task Scheduler at this file (docs/RUNNING.md).
REM Add --paper to run against live prices without sending a single order.
cd /d "%~dp0.."
if not exist logs mkdir logs
.venv\Scripts\python.exe -m tradeapp serve >> logs\core.log 2>&1
