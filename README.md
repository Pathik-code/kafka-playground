# Kafka Playground 🚀

A robust, Docker-based Kafka environment with a custom Control UI and Jupyter Notebook integration.

## Features

- **Multi-Broker Cluster**: Default 3-broker setup (kafka1, kafka2, kafka3).
- **Control UI**:
  - Dashboard with real-time broker status.
  - Dynamic broker management (Add/Remove brokers).
  - **Reset Docker**: One-click cleanup and restart of the entire environment.
- **Jupyter Integration**: Pre-configured environment for Kafka Python development.
- **Kafka UI**: Integrated Provectus Kafka UI for topic/message management.

## Rapid Start

```bash
docker-compose up -d --build
```

- **Control Panel**: [http://localhost:5001](http://localhost:5001)
- **Jupyter Lab**: [http://localhost:8888](http://localhost:8888)
- **Kafka UI**: [http://localhost:8080](http://localhost:8080)

## Resetting the Environment

If the cluster gets into an unstable state or you want to start fresh:

1. Go to the Control Panel.
2. Click the **Reset Docker** button (top right).
3. This will:
   - Clean `docker-compose.yml` to default services.
   - Remove custom brokers and volumes.
   - Restart the cluster (preserving Jupyter notebooks).

## Development

The project structure:
- `control-ui/`: Flask backend + Vanilla JS frontend.
- `jupyter/`: Custom Jupyter image config.
- `docker-compose.yml`: Main orchestration file.

### Backend API
The Control UI exposes an API at `http://localhost:5001/api`.
- `GET /api/cluster/status`: Get broker count and status.
- `POST /api/cluster/reset`: execution background reset.
