@echo off
REM Kafka Playground - Shutdown Script for Windows

echo.
echo ========================================
echo   Stopping Kafka Playground
echo ========================================
echo.

docker-compose down

echo.
echo ========================================
echo   All services stopped successfully
echo ========================================
echo.
echo Press any key to exit...
pause > nul
