FROM jupyter/base-notebook:latest

USER root

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

USER jovyan

# Install Python packages for Kafka development
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Create work directory
RUN mkdir -p /home/jovyan/work

# Set working directory
WORKDIR /home/jovyan/work

# Expose Jupyter port
EXPOSE 8888
