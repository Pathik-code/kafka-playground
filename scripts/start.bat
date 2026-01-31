@echo off
REM Kafka Playground - Startup Script for Windows

echo.
echo ========================================
echo   Kafka Development Playground
echo ========================================
echo.
echo Starting Kafka cluster...
echo.

REM Start all services
docker-compose up -d

REM Wait for services to initialize
echo.
echo Waiting for services to start (30 seconds)...
timeout /t 30 /nobreak > nul

REM Check status
echo.
echo Checking service status...
docker-compose ps

echo.
echo ========================================
echo   Kafka Playground is Ready!
echo ========================================
echo.
echo Available interfaces:
echo   - Control UI:  http://localhost:5000
echo   - Kafka UI:    http://localhost:8080
echo   - Jupyter:     http://localhost:8888
echo.
echo Press any key to exit...
pause > nul
