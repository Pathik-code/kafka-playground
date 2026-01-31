#!/bin/bash
# Kafka Playground - Shutdown Script for Linux/Mac

echo ""
echo "========================================"
echo "   Stopping Kafka Playground"
echo "========================================"
echo ""

docker-compose down

echo ""
echo "========================================"
echo "   All services stopped successfully"
echo "========================================"
echo ""
