#!/bin/bash
# Kafka Playground - Startup Script for Linux/Mac

echo ""
echo "========================================"
echo "   Kafka Development Playground"
echo "========================================"
echo ""
echo "Starting Kafka cluster..."
echo ""

# Start all services
docker-compose up -d

# Wait for services to initialize
echo ""
echo "Waiting for services to start (30 seconds)..."
sleep 30

# Check status
echo ""
echo "Checking service status..."
docker-compose ps

echo ""
echo "========================================"
echo "   Kafka Playground is Ready!"
echo "========================================"
echo ""
echo "Available interfaces:"
echo "  - Control UI:  http://localhost:5000"
echo "  - Kafka UI:    http://localhost:8080"
echo "  - Jupyter:     http://localhost:8888"
echo ""
