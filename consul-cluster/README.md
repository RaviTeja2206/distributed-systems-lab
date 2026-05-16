# Consul Cluster

A 3-node Consul cluster demonstrating Raft-based service discovery,
health checking, leader election, and dynamic service registration.

## Architecture

- consul-1: server, bootstrap-expect=3, UI exposed on port 8500
- consul-2: server, voter
- consul-3: server, voter

Consensus: Raft (same algorithm used in etcd and Kubernetes)
Quorum: 2 of 3 nodes required for writes
Gossip: Serf protocol for member failure detection

## Prerequisites

- Docker and Docker Compose

## Start the cluster

Unlike Galera, all 3 Consul nodes start simultaneously. Raft handles
simultaneous startup correctly - nodes discover each other via retry-join
and elect a leader once quorum is reached.

    docker compose -f docker-compose.yml up -d

    # Verify all 3 nodes and current Raft leader
    docker exec consul-cluster-consul-1-1 consul members
    docker exec consul-cluster-consul-1-1 consul operator raft list-peers

Expected healthy output for members:

    Node      Address          Status  Type
    consul-1  10.30.0.11:8301  alive   server
    consul-2  10.30.0.12:8301  alive   server
    consul-3  10.30.0.13:8301  alive   server

Expected raft list-peers output:

    Node      State     Voter
    consul-2  leader    true
    consul-1  follower  true
    consul-3  follower  true

## Management UI

    http://localhost:8500

## Register services

Services register with Consul on startup via the HTTP API. Other services
query Consul for addresses rather than using hardcoded IPs.

    # Register a RabbitMQ node
    curl -X PUT http://localhost:8500/v1/catalog/register \
      -H "Content-Type: application/json" \
      -d '{
        "Node": "rabbitmq-node-1",
        "Address": "10.10.0.11",
        "Service": {
          "ID": "rabbitmq-1",
          "Service": "rabbitmq",
          "Address": "10.10.0.11",
          "Port": 5672,
          "Tags": ["messaging", "amqp"]
        }
      }'

    # Register a Galera node
    curl -X PUT http://localhost:8500/v1/catalog/register \
      -H "Content-Type: application/json" \
      -d '{
        "Node": "galera-node-1",
        "Address": "10.20.0.11",
        "Service": {
          "ID": "galera-1",
          "Service": "mariadb",
          "Address": "10.20.0.11",
          "Port": 3306,
          "Tags": ["database", "galera"]
        }
      }'

## Query services

    # List all registered service names
    curl http://localhost:8500/v1/catalog/services | python3 -m json.tool

    # Get all rabbitmq instances with addresses
    curl http://localhost:8500/v1/catalog/service/rabbitmq | python3 -m json.tool

    # Get only healthy instances (passing health checks)
    curl "http://localhost:8500/v1/health/service/rabbitmq?passing=true" | python3 -m json.tool

    # Python service discovery - what a worker does on startup
    curl -s http://localhost:8500/v1/catalog/service/rabbitmq \
      | python3 -c "
import json, sys
services = json.load(sys.stdin)
print('Available RabbitMQ nodes:')
for s in services:
    print(f'  {s[chr(34)]ServiceAddress{chr(34)}}:{s[chr(34)]ServicePort{chr(34)]}')
"

## Key concepts demonstrated

**Raft consensus** - Consul uses Raft for leader election among server nodes.
All 3 nodes start simultaneously and elect a leader within 5 seconds once
quorum is reached. Compare this to Galera which requires sequential startup
due to SST conflicts.

**Two-layer detection** - Consul uses two separate protocols. Serf gossip
detects member failures via heartbeats (fast, eventually consistent). Raft
handles consistent writes to the catalog (slower, strongly consistent).
Failure detection and leader election are separate subsystems.

**Service registration patterns:**
- Self-registration: service calls Consul API on startup (RabbitMQ plugin)
- Sidecar: separate agent watches Docker socket and registers containers
- External: infrastructure tooling registers services after deployment

**Consul vs ZooKeeper** - both provide distributed coordination. ZooKeeper
uses ZAB consensus and ephemeral nodes for leader election. Consul uses Raft
and provides a full service registry with health checking. In the MAM
broadcast architecture ZooKeeper handles Mesos master election while Consul
handles service discovery. Different problems, different tools.

**Health checking** - Consul continuously polls registered services via TCP,
HTTP, or script checks. Failed checks remove the service from healthy query
results. The deregister_critical_service_after setting automatically removes
services that have been failing for a configured duration.

## Failure experiments

### Experiment 1 - leader election timing

    # Find current leader
    docker exec consul-cluster-consul-1-1 consul operator raft list-peers

    # Kill the leader (adjust node name based on above output)
    docker compose -f docker-compose.yml stop consul-2

    # Watch election in logs - completes in under 5 seconds
    docker compose -f docker-compose.yml logs consul-1 --tail=30

    # Verify new leader elected
    docker exec consul-cluster-consul-1-1 consul operator raft list-peers

Expected log sequence:

    heartbeat timeout reached, starting election
    entering candidate state: term=2
    New leader elected: consul-3

Election completes in 2-5 seconds. The log shows two separate subsystems:
Serf gossip suspects and confirms the failure, then Raft triggers election.

### Experiment 2 - quorum loss

    # Kill two nodes - lose quorum
    docker compose -f docker-compose.yml stop consul-2 consul-3

    # Attempt a write - should fail immediately
    curl -X PUT http://localhost:8500/v1/catalog/register \
      -d '{"Node":"test","Address":"1.2.3.4"}'

    # Expected response: No cluster leader

    # Check member status from surviving node
    docker exec consul-cluster-consul-1-1 consul members

Expected: write rejected with No cluster leader. consul-2 and consul-3
show as failed in members list. This is identical CP behavior to Galera -
consistency over availability when quorum is lost.

### Experiment 3 - recovery after quorum loss

    # Restart the stopped nodes
    docker compose -f docker-compose.yml start consul-2 consul-3

    # Wait 15 seconds then verify full recovery
    docker exec consul-cluster-consul-1-1 consul members
    docker exec consul-cluster-consul-1-1 consul operator raft list-peers

Expected: all 3 nodes alive, MaxFollowerLag=0, cluster accepting writes again.

## Teardown

    docker compose -f docker-compose.yml down -v

## Troubleshooting

**ACL support disabled errors in logs:**
The UI attempts to load ACL tokens. These errors are harmless in a lab
environment without ACL configuration enabled. Ignore them.

**Service shows critical health status:**
The registered service address has nothing actually running on it. This is
expected when registering simulated services for discovery learning. In
production services register themselves with their real address.

**consul operator raft list-peers hangs:**
The cluster has lost quorum and cannot process reads. Check consul members
to confirm which nodes are alive. Restart failed nodes to restore quorum.
