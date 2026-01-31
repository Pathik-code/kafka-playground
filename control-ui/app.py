from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import docker
import os
import yaml
import subprocess
import time
import socket
import threading
from kafka import KafkaAdminClient, KafkaConsumer
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError, UnknownTopicOrPartitionError
import logging

import copy

class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Docker client
docker_client = docker.from_env()

# Constants
DOCKER_COMPOSE_PATH = '/app/docker-compose.yml'
DEFAULT_CLUSTER_ID = 'default'

def get_kafka_bootstrap_servers(cluster_id=DEFAULT_CLUSTER_ID):
    """Dynamically get bootstrap servers for a specific cluster"""
    try:
        if os.path.exists(DOCKER_COMPOSE_PATH):
            with open(DOCKER_COMPOSE_PATH, 'r') as f:
                compose = yaml.safe_load(f)
            
            brokers = []
            for service_name, service in compose.get('services', {}).items():
                # Filter by cluster logic
                if cluster_id == DEFAULT_CLUSTER_ID:
                    # Match old style "kafka1", "kafka2" OR "kafka-default-1"? 
                    # Let's stick to legacy names "kafkaN" for default cluster
                     is_target = service_name.startswith('kafka') and service_name != 'kafka-ui' and '-' not in service_name
                else:
                    # New style: kafka-{cluster_id}-{broker_id}
                    is_target = service_name.startswith(f"kafka-{cluster_id}-")
                
                if is_target:
                    try:
                        # Extract port from ports definition
                        # Assumption: first port mapping is EXTERNAL_PORT:9092
                        ports = service.get('ports', [])
                        if ports:
                            ext_port = ports[0].split(':')[0]
                            # Use localhost for internal connectivity check or container name if within network
                            # Since control-ui is in network, use Service Name : Internal Port?
                            # But bootstrap_servers usually need accessible address. 
                            # If we use container names, we need internal ports (2909x)
                            # Let's parse internal port from KAFKA_ADVERTISED_LISTENERS if possible
                            env = service.get('environment', {})
                            listeners = env.get('KAFKA_ADVERTISED_LISTENERS', '')
                            # Example: PLAINTEXT://kafka1:29092
                            for l in listeners.split(','):
                                if 'PLAINTEXT://' in l:
                                    # Extract kafka1:29092
                                    addr = l.replace('PLAINTEXT://', '')
                                    brokers.append(addr)
                                    break
                    except Exception:
                        continue
            
            if brokers:
                return ','.join(brokers)
    except Exception as e:
        logger.error(f"Error parsing docker-compose for bootstrap servers: {e}")
    
    # Fallback only for default
    if cluster_id == DEFAULT_CLUSTER_ID:
        return os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka1:29092,kafka2:29093,kafka3:29094')
    return ""

def get_kafka_admin(cluster_id=DEFAULT_CLUSTER_ID):
    """Get Kafka admin client"""
    try:
        servers = get_kafka_bootstrap_servers(cluster_id)
        if not servers: return None
        return KafkaAdminClient(
            bootstrap_servers=servers.split(','),
            client_id=f'control-ui-admin-{cluster_id}',
            request_timeout_ms=5000
        )
    except Exception as e:
        logger.error(f"Failed to create Kafka admin client for {cluster_id}: {e}")
        return None

@app.route('/')
def index():
    """Render main dashboard"""
    return render_template('index.html')

@app.route('/api/clusters', methods=['GET'])
def list_clusters():
    """List all clusters and their basic info"""
    try:
        if not os.path.exists(DOCKER_COMPOSE_PATH):
             return jsonify({'success': False, 'error': 'docker-compose.yml not found'}), 500

        with open(DOCKER_COMPOSE_PATH, 'r') as f:
            compose = yaml.safe_load(f)
            
        clusters = {DEFAULT_CLUSTER_ID: {'name': 'Default Cluster', 'brokers': 0, 'status': 'unknown'}}
        
        # Scan services to find clusters
        services = compose.get('services', {})
        for name, service in services.items():
            if name.startswith('kafka') and name != 'kafka-ui':
                if '-' not in name:
                    # Default cluster (kafka1, kafka2)
                    clusters[DEFAULT_CLUSTER_ID]['brokers'] += 1
                else:
                    # Named cluster: kafka-{cluster_id}-{broker_id}
                    parts = name.split('-')
                    if len(parts) >= 3:
                        cluster_id = parts[1]
                        if cluster_id not in clusters:
                            clusters[cluster_id] = {'name': cluster_id, 'brokers': 0, 'status': 'unknown'}
                        clusters[cluster_id]['brokers'] += 1

        # Check status for each (simple check)
        for cid, info in clusters.items():
             # Logic to check if at least one broker is running
             bs = get_kafka_bootstrap_servers(cid)
             if bs:
                 info['status'] = 'configured'
                 # We could check actual container status here
                 
        return jsonify({'success': True, 'clusters': clusters})
    except Exception as e:
        logger.error(f"Error listing clusters: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/clusters', methods=['POST'])
def create_cluster():
    """Create a new cluster"""
    try:
        data = request.json
        cluster_id = data.get('name')
        if not cluster_id or not cluster_id.isalnum():
             return jsonify({'success': False, 'error': 'Invalid cluster name'}), 400
             
        if cluster_id == DEFAULT_CLUSTER_ID:
            return jsonify({'success': False, 'error': 'Cannot create default cluster'}), 400

        # We essentially add 1 broker to start this cluster
        return add_broker_internal(cluster_id)
        
    except Exception as e:
         return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/clusters/<cluster_id>', methods=['DELETE'])
def delete_cluster(cluster_id):
    """Delete an entire cluster"""
    if cluster_id == DEFAULT_CLUSTER_ID:
        return jsonify({'success': False, 'error': 'Cannot delete default cluster'}), 400
        
    try:
        with open(DOCKER_COMPOSE_PATH, 'r') as f:
            compose = yaml.safe_load(f)
            
        services_to_remove = []
        volumes_to_remove = []
        
        for name in list(compose.get('services', {}).keys()):
             if name.startswith(f"kafka-{cluster_id}-"):
                 services_to_remove.append(name)
                 
                 # Queue volume for removal
                 volumes_to_remove.append(f"{name}-data")

        if not services_to_remove:
            return jsonify({'success': False, 'error': 'Cluster not found'}), 404

        # Remove services
        for name in services_to_remove:
            # Stop container
            try:
                container = docker_client.containers.get(name)
                container.kill()
                logger.info(f"Stopped container {name}")
            except docker.errors.NotFound:
                logger.debug(f"Container {name} not found, skipping")
            except Exception as e:
                logger.warning(f"Could not stop container {name}: {e}")
            
            del compose['services'][name]
            
        # Remove volumes
        for vol in volumes_to_remove:
            if 'volumes' in compose and vol in compose['volumes']:
                del compose['volumes'][vol]
        
        save_docker_compose(compose)
            
        # Prune - use shell=True for better compatibility
        try:
            result = subprocess.run(
                "docker-compose up -d --remove-orphans",
                shell=True,
                capture_output=True,
                text=True,
                cwd="/app",
                timeout=60
            )
            if result.returncode != 0:
                logger.warning(f"docker-compose prune returned {result.returncode}: {result.stderr}")
        except Exception as e:
            logger.error(f"Error running docker-compose: {e}")
        
        # Delete actual Docker volumes
        for vol in volumes_to_remove:
            try:
                # Volume names may have project prefix
                full_vol_name = f"kafka-playground-{vol}"
                docker_client.volumes.get(full_vol_name).remove(force=True)
                logger.info(f"Deleted volume: {full_vol_name}")
            except docker.errors.NotFound:
                logger.debug(f"Volume {vol} not found, skipping")
            except Exception as e:
                logger.warning(f"Could not delete volume {vol}: {e}")
        
        return jsonify({'success': True, 'message': f'Cluster {cluster_id} deleted'})
        
    except Exception as e:
        logger.error(f"Error deleting cluster: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cluster/status', methods=['GET'])
def get_cluster_status():
    """Get status for a specific cluster (defaults to 'default')"""
    cluster_id = request.args.get('cluster_id', DEFAULT_CLUSTER_ID)
    
    try:
        # Discover containers belonging to this cluster
        containers = {}
        
        # Zookeeper is shared
        try:
            zk = docker_client.containers.get('zookeeper')
            containers['zookeeper'] = {'status': zk.status, 'state': zk.attrs['State']}
        except:
            containers['zookeeper'] = {'status': 'not_found', 'state': {}}

        # Find brokers for this cluster
        all_containers = docker_client.containers.list(all=True)
        for c in all_containers:
            try:
                # Match name
                is_match = False
                if cluster_id == DEFAULT_CLUSTER_ID:
                    if c.name.startswith('kafka') and c.name != 'kafka-ui' and c.name[5:].isdigit():
                        is_match = True
                else:
                     if c.name.startswith(f"kafka-{cluster_id}-"):
                         is_match = True
                
                if is_match:
                    containers[c.name] = {
                        'status': c.status,
                        'state': c.attrs['State']
                    }
            except docker.errors.NotFound:
                continue # Container likely deleted during iteration

        # Metrics - count brokers from containers (instant) not Kafka connection
        topic_count = 0
        # Count kafka brokers from containers dict (excludes zookeeper and kafka-ui)
        broker_count = len([k for k in containers.keys() if k.startswith('kafka')])
        
        bs = get_kafka_bootstrap_servers(cluster_id)
        if bs:
            admin = get_kafka_admin(cluster_id)
            if admin:
                try:
                    topics = admin.list_topics()
                    topic_count = len([t for t in topics if not t.startswith('_')])
                except Exception as e:
                    logger.warning(f"Kafka connection warning for {cluster_id}: {e}")
                finally:
                    admin.close()
        
        return jsonify({
            'success': True,
            'cluster_id': cluster_id,
            'containers': containers,
            'topic_count': topic_count,
            'broker_count': broker_count,
            'kafka_servers': bs
        })
    except Exception as e:
        logger.error(f"Error getting cluster status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Port management
def is_port_available(port):
    """Check if a port is likely available by trying to connect to it.
    If connection is refused, port is available. If connection succeeds, port is in use.
    Note: This works from inside Docker container to check host ports."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            # Try to connect to host.docker.internal (Docker Desktop) or localhost
            result = s.connect_ex(('host.docker.internal', port))
            if result == 0:
                # Connection succeeded = port is in use
                logger.debug(f"Port {port} is in use (connection succeeded)")
                return False
            else:
                # Connection refused = port is available
                logger.debug(f"Port {port} is available (connection refused)")
                return True
    except socket.error as e:
        # If we can't connect at all, assume port is available
        logger.debug(f"Port {port} check error (assuming available): {e}")
        return True

def get_next_free_port(compose_data, start_port=9095):
    """Find next available port - checks docker-compose.yml for used ports"""
    used_ports = set()
    services = compose_data.get('services', {})
    for s in services.values():
        for p in s.get('ports', []):
            try:
                # Handle "9092:9092" or "${PORT}:9092"
                port_str = str(p).split(':')[0]
                if '$' in port_str: 
                    continue  # Skip env vars
                used_ports.add(int(port_str))
            except Exception as e:
                logger.debug(f"Could not parse port {p}: {e}")
                continue
    
    candidate = start_port
    max_attempts = 100  # Prevent infinite loop
    
    for _ in range(max_attempts):
        if candidate not in used_ports:
            # Skip actual port check from container - just check docker-compose
            logger.info(f"Found available port: {candidate}")
            return candidate
        candidate += 1
    
    # Fallback - return the candidate
    logger.warning(f"Could not find free port after {max_attempts} attempts, using {candidate}")
    return candidate

# Reuse helper
def add_broker_internal(cluster_id):
    try:
        if not os.path.exists(DOCKER_COMPOSE_PATH):
            return jsonify({'success': False, 'error': 'docker-compose.yml not found'}), 500

        with open(DOCKER_COMPOSE_PATH, 'r') as f:
            compose = yaml.safe_load(f)

        services = compose.get('services', {})
        
        # Determine Naming and IDs
        current_brokers = []
        for s in services.keys():
            if cluster_id == DEFAULT_CLUSTER_ID:
                if s.startswith('kafka') and s != 'kafka-ui' and '-' not in s:
                    current_brokers.append(s)
            else:
                if s.startswith(f"kafka-{cluster_id}-"):
                    current_brokers.append(s)
        
        last_id = 0
        for b in current_brokers:
            try:
                # kafkaN or kafka-cluster-N
                if cluster_id == DEFAULT_CLUSTER_ID:
                    bid = int(b.replace('kafka', ''))
                else:
                    bid = int(b.split('-')[-1])
                if bid > last_id: last_id = bid
            except: pass
            
        new_id = last_id + 1
        
        # Naming
        if cluster_id == DEFAULT_CLUSTER_ID:
            new_name = f"kafka{new_id}"
            template_svc_name = current_brokers[0] if current_brokers else 'kafka1'
        else:
            new_name = f"kafka-{cluster_id}-{new_id}"
            # Template: Try to copy from same cluster, else copy from default kafka1
            template_svc_name = current_brokers[0] if current_brokers else 'kafka1'

        new_service = copy.deepcopy(services[template_svc_name])
        
        # Configuration Logic
        new_service['container_name'] = new_name
        new_service['hostname'] = new_name
        
        # Find free port
        new_external_port = get_next_free_port(compose)
        new_service['ports'] = [f"{new_external_port}:9092"]
        
        # Environment
        env = new_service.get('environment', {})
        env['KAFKA_BROKER_ID'] = new_id
        
        # Internal Port Logic: Standardize to 29092 for simplicity
        # Update Advertised Listeners
        # "PLAINTEXT://{new_name}:29092,PLAINTEXT_HOST://localhost:{new_external_port}"
        # Zookeeper Chroot
        base_zk = "zookeeper:2181"
        if cluster_id != DEFAULT_CLUSTER_ID:
             # Add chroot
             env['KAFKA_ZOOKEEPER_CONNECT'] = f"{base_zk}/{cluster_id}"
        else:
             env['KAFKA_ZOOKEEPER_CONNECT'] = base_zk

        env['KAFKA_ADVERTISED_LISTENERS'] = \
            f"PLAINTEXT://{new_name}:29092,PLAINTEXT_HOST://localhost:{new_external_port}"
            
        new_service['environment'] = env
        
        # Volumes
        new_volumes = []
        for v in new_service.get('volumes', []):
            if ':' in v:
                src, dest = v.split(':')
                # Replace volume name
                if 'kafka' in src:
                     new_src = f"{new_name}-data"
                     new_volumes.append(f"{new_src}:{dest}")
                     
                     if 'volumes' not in compose: compose['volumes'] = {}
                     compose['volumes'][new_src] = {'name': f"kafka-playground-{new_name}-data"}
                else:
                    new_volumes.append(v)
        new_service['volumes'] = new_volumes
        
        # Dependencies
        new_service['depends_on'] = ['zookeeper'] # Reset to just zk to avoid chains

        # Save
        compose['services'][new_name] = new_service
        
        # If this is the FIRST broker of a new cluster, we might need to update KafkaUI?
        # Maybe later. Kafka UI doesn't allow dynamic config via Env Var reload easily without restart.
        
        save_docker_compose(compose)

            
        # Up (Restart/Apply changes) - use shell=True for better compatibility
        try:
            result = subprocess.run(
                "docker-compose up -d " + new_name,
                shell=True,
                capture_output=True,
                text=True,
                cwd="/app",
                timeout=60
            )
            if result.returncode != 0:
                logger.warning(f"docker-compose up returned {result.returncode}: {result.stderr}")
        except subprocess.TimeoutExpired:
            logger.warning("docker-compose up timed out, container may still be starting")
        except Exception as e:
            logger.error(f"Error running docker-compose: {e}")
        
        return jsonify({
            'success': True,
            'message': f'Broker {new_name} added to {cluster_id}',
            'broker': {'name': new_name, 'port': new_external_port}
        })

    except Exception as e:
        logger.error(f"Error adding broker: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Helper for safe writing (preserves inode for bind mounts)
def save_docker_compose(compose_data):
    try:
        with open(DOCKER_COMPOSE_PATH, 'r+') as f:
            f.seek(0)
            f.truncate()
            yaml.dump(compose_data, f, Dumper=NoAliasDumper, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        logger.error(f"Error saving docker-compose: {e}")
        raise

@app.route('/api/brokers/add', methods=['POST'])
def add_broker():
    data = request.json or {}
    cluster_id = data.get('cluster_id', DEFAULT_CLUSTER_ID)
    return add_broker_internal(cluster_id)

@app.route('/api/brokers/<broker_id>', methods=['DELETE'])
def delete_broker(broker_id):
    # Try to guess cluster from name if passed?
    # Broker ID in URL might be "1" (default) or "kafka-test-1"
    # Let's assume ID is the full Service Name if it contains '-'
    # OR it is just the suffix.
    # To be safe, look for the service.
    
    try:
        broker_name = broker_id
        if broker_id.isdigit():
             broker_name = f"kafka{broker_id}"
             
        # Guard: Protect default brokers
        if broker_name in ['kafka1', 'kafka2', 'kafka3']:
            return jsonify({'success': False, 'error': 'Cannot delete default brokers'}), 403
             
        # Check if exists
        with open(DOCKER_COMPOSE_PATH, 'r') as f:
            compose = yaml.safe_load(f)
            
        if broker_name not in compose['services']:
             return jsonify({'success': False, 'error': 'Broker not found'}), 404
             
        # Stop and Remove logic
        try:
            container = docker_client.containers.get(broker_name)
            container.stop()
            container.remove()
            logger.info(f"Stopped and removed container {broker_name}")
        except docker.errors.NotFound:
            logger.debug(f"Container {broker_name} not found, skipping")
        except Exception as e:
            logger.warning(f"Could not stop/remove container {broker_name}: {e}")
        
        del compose['services'][broker_name]
        vol_name = f"{broker_name}-data"
        if 'volumes' in compose and vol_name in compose['volumes']:
             del compose['volumes'][vol_name]
             
        save_docker_compose(compose)
            
        try:
            result = subprocess.run(
                "docker-compose up -d --remove-orphans",
                shell=True,
                capture_output=True,
                text=True,
                cwd="/app",
                timeout=60
            )
            if result.returncode != 0:
                logger.warning(f"docker-compose returned {result.returncode}: {result.stderr}")
        except Exception as e:
            logger.error(f"Error running docker-compose: {e}")
        
        # Delete actual Docker volume
        try:
            full_vol_name = f"kafka-playground-{vol_name}"
            docker_client.volumes.get(full_vol_name).remove(force=True)
            logger.info(f"Deleted volume: {full_vol_name}")
        except docker.errors.NotFound:
            logger.debug(f"Volume {vol_name} not found, skipping")
        except Exception as e:
            logger.warning(f"Could not delete volume {vol_name}: {e}")
        
        return jsonify({'success': True, 'message': 'Broker deleted'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/brokers/<broker_id>/start', methods=['POST'])
def start_broker(broker_id):
    # start container logic
    try:
        container = docker_client.containers.get(broker_id) # Require full name
        container.start()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
        
@app.route('/api/brokers/<broker_id>/stop', methods=['POST'])
def stop_broker(broker_id):
    try:
        container = docker_client.containers.get(broker_id)
        container.stop()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Topics... need cluster_id context!
@app.route('/api/topics/list', methods=['GET'])
def list_topics():
    cluster_id = request.args.get('cluster_id', DEFAULT_CLUSTER_ID)
    try:
        admin = get_kafka_admin(cluster_id)
        if not admin: return jsonify({'success': False, 'error': 'No admin'}), 500
        
        topics = admin.list_topics()
        user_topics = [t for t in topics if not t.startswith('_')]
        
        # Simplified details (count only) to speed up?
        # Or keeping full details
        topic_details = []
        bs = get_kafka_bootstrap_servers(cluster_id)
        consumer = KafkaConsumer(bootstrap_servers=bs.split(','), group_id=f'topic-list-{cluster_id}')
        for t in user_topics:
            p = consumer.partitions_for_topic(t)
            topic_details.append({'name': t, 'partitions': len(p) if p else 0})
        consumer.close()
        admin.close()
        return jsonify({'success': True, 'topics': topic_details})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/topics/create', methods=['POST'])
def create_topic():
    data = request.json
    cluster_id = data.get('cluster_id', DEFAULT_CLUSTER_ID)
    topic_name = data.get('name')
    # ... Use cluster_id in get_kafka_admin()
    try:
         admin = get_kafka_admin(cluster_id)
         new_topic = NewTopic(name=topic_name, num_partitions=int(data.get('partitions', 3)), replication_factor=int(data.get('replication_factor', 1)))
         admin.create_topics([new_topic])
         admin.close()
         return jsonify({'success': True})
    except Exception as e:
         return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/topics/delete/<topic_name>', methods=['DELETE'])
def delete_topic(topic_name):
    cluster_id = request.args.get('cluster_id', DEFAULT_CLUSTER_ID)
    try:
        admin = get_kafka_admin(cluster_id)
        admin.delete_topics([topic_name])
        admin.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cluster/stop', methods=['POST'])
def stop_cluster():
    # Stop everything? Or just current cluster?
    # Usually "Stop Cluster" button implies stopping the whole environment or the specific cluster.
    # Let's support specific cluster stop
    data = request.json or {}
    cluster_id = data.get('cluster_id')
    
    if not cluster_id:
        # Legacy behavior: Stop everything related to default cluster + core
        containers = ['zookeeper', 'kafka-ui', 'jupyter-kafka', 'control-ui']
        # And all kafka*
        for c in docker_client.containers.list(all=True):
            if c.name.startswith('kafka'): containers.append(c.name)
    else:
        # Stop only cluster brokers
        containers = []
        for c in docker_client.containers.list(all=True):
            if c.name.startswith(f"kafka-{cluster_id}-"): containers.append(c.name)
            
    stopped = []
    for name in containers:
        try:
            c = docker_client.containers.get(name)
            if c.status == 'running':
                c.stop()
                stopped.append(name)
                logger.info(f"Stopped container {name}")
        except docker.errors.NotFound:
            logger.debug(f"Container {name} not found")
        except Exception as e:
            logger.warning(f"Could not stop container {name}: {e}")
    return jsonify({'success': True, 'stopped': stopped})

@app.route('/api/cluster/start', methods=['POST'])
def start_cluster():
    # Start all services
    try:
        result = subprocess.run(
            "docker-compose up -d",
            shell=True,
            capture_output=True,
            text=True,
            cwd="/app",
            timeout=120
        )
        if result.returncode != 0:
            logger.warning(f"docker-compose up returned {result.returncode}: {result.stderr}")
        return jsonify({'success': True})
    except Exception as e:
         return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cluster/reset', methods=['POST'])
def reset_cluster():
    """Reset Docker setup - async background task"""
    def run_reset():
        logger.info("Starting background reset...")
        time.sleep(1) # Give API time to respond
        
        # 1. Revert docker-compose.yml to default state (keep only default services)
        try:
            if os.path.exists(DOCKER_COMPOSE_PATH):
                with open(DOCKER_COMPOSE_PATH, 'r') as f:
                    compose = yaml.safe_load(f)
                
                # Default services that should persist
                defaults = ['zookeeper', 'kafka1', 'kafka2', 'kafka3', 'control-panel', 'headers', 'header-cluster', 'kafka-ui', 'jupyter', 'jupyter-kafka']
                
                # Remove extra services
                services = compose.get('services', {})
                to_remove = [s for s in services if s not in defaults]
                for s in to_remove:
                    del services[s]
                
                # Remove extra volumes
                volumes = compose.get('volumes', {})
                vol_defaults = ['zookeeper-data', 'zookeeper-logs', 'kafka1-data', 'kafka2-data', 'kafka3-data']
                vol_remove = [v for v in volumes if v not in vol_defaults]
                for v in vol_remove:
                    del volumes[v]
                    
                save_docker_compose(compose)
                logger.info(f"Cleaned docker-compose.yml. Removed services: {to_remove}")
        except Exception as e:
            logger.error(f"Failed to clean docker-compose.yml: {e}")

        # 2. Stop and Remove
        try:
             subprocess.run("docker-compose down -v --remove-orphans", shell=True, cwd="/app")
        except Exception as e:
             logger.error(f"Down failed: {e}")

        # 3. Prune specific resources
        try:
            # Containers
            for c in docker_client.containers.list(all=True):
                if 'kafka-playground' in c.name or c.name.startswith('kafka'):
                     try: c.remove(force=True)
                     except: pass
            
            # Volumes
            for v in docker_client.volumes.list():
                if 'kafka-playground' in v.name or v.name.startswith('kafka'):
                     try: v.remove(force=True)
                     except: pass
                     
            # Networks
            try: docker_client.networks.prune()
            except: pass
            
        except Exception as e:
            logger.error(f"Prune failed: {e}")

        # 4. Restart
        logger.info("Restarting services...")
        try:
            subprocess.run("docker-compose up -d", shell=True, cwd="/app")
        except Exception as e:
            logger.error(f"Restart failed: {e}")

    # Start background thread
    thread = threading.Thread(target=run_reset)
    thread.start()
    
    return jsonify({
        'success': True, 
        'message': 'Reset started in background. UI will disconnect briefly.',
        'actions': ['Cleaning config', 'Stopping containers', 'Pruning resources', 'Restarting services']
    })

@app.route('/api/config/validate', methods=['GET'])
def validate_config():
    """Validate current docker-compose.yml configuration for port conflicts"""
    try:
        if not os.path.exists(DOCKER_COMPOSE_PATH):
            return jsonify({'success': False, 'error': 'docker-compose.yml not found'}), 500
        
        with open(DOCKER_COMPOSE_PATH, 'r') as f:
            compose = yaml.safe_load(f)
        
        issues = []
        port_usage = {}  # port -> [service names]
        
        for service_name, service in compose.get('services', {}).items():
            for p in service.get('ports', []):
                try:
                    port_str = p.split(':')[0]
                    if '$' in port_str:
                        continue
                    port = int(port_str)
                    if port not in port_usage:
                        port_usage[port] = []
                    port_usage[port].append(service_name)
                except:
                    continue
        
        # Check for duplicate ports in config
        for port, services in port_usage.items():
            if len(services) > 1:
                issues.append(f"Port {port} is used by multiple services: {', '.join(services)}")
        
        # Check if ports are available on host
        for port in port_usage.keys():
            if not is_port_available(port):
                issues.append(f"Port {port} is already in use on the host system")
        
        return jsonify({
            'success': True,
            'valid': len(issues) == 0,
            'issues': issues,
            'port_map': {str(k): v for k, v in port_usage.items()}
        })
    except Exception as e:
        logger.error(f"Error validating config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
