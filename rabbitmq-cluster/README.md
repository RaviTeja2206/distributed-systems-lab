# RabbitMQ Cluster

A 3-node RabbitMQ cluster with synchronous HA mirroring, demonstrating
message broker clustering, consumer patterns, and failure recovery.

## Architecture

- rabbitmq-1: disc node, queue master candidates
- rabbitmq-2: disc node
- rabbitmq-3: disc node

All nodes connected via Erlang distribution protocol.
HA policy: ha-all (queues mirrored across all 3 nodes)
Partition handling: pause_minority

## Prerequisites

- Docker and Docker Compose
- Python 3.10+ with pika: pip install pika

## Start the cluster

    docker compose -f docker-compose.yml up -d

    # Wait 60 seconds then verify
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl cluster_status

Expected healthy output:

    Disk Nodes: rabbit@rabbitmq-1, rabbit@rabbitmq-2, rabbit@rabbitmq-3
    Running Nodes: rabbit@rabbitmq-1, rabbit@rabbitmq-2, rabbit@rabbitmq-3
    Network Partitions: (none)

## Management UI

    URL:      http://localhost:15672
    Username: admin
    Password: password

## Run producer and consumer

    # Terminal 1 - start consumer
    python3 python/consumer.py

    # Terminal 2 - publish messages
    python3 python/producer.py

## Key concepts demonstrated

**Erlang cookie** - shared secret required for Erlang nodes to authenticate
each other. All nodes and CLI tools must use the identical cookie or
clustering silently fails. Implemented via entrypoint script to avoid
macOS Docker Desktop file permission issues with bind mounts.

**HA mirroring** - the ha-all policy mirrors every queue across all nodes.
When the queue master fails a mirror is promoted automatically. Applied via:

    rabbitmqctl set_policy ha-all '.*' '{"ha-mode":"all"}'

**pause_minority** - partition handling strategy. If a network partition
splits the cluster the minority side pauses rather than accepting writes
that could diverge from the majority. Prevents split-brain.

**Consumer acknowledgment** - consumers use basic_ack to confirm processing.
If a consumer dies before acking RabbitMQ re-queues the message automatically.
prefetch_count=1 ensures fair dispatch - a slow consumer gets fewer messages
than a fast one.

## Failure experiments

### Experiment 1 - kill a non-master node

    # Check which node holds the queue master
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl list_queues name pid slave_pids

    # Kill a non-master node
    docker compose -f docker-compose.yml stop rabbitmq-2

    # Verify cluster still serving (2 of 3 nodes = quorum maintained)
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl cluster_status

Expected: cluster_size drops to 2, rabbitmq-2 absent from Running Nodes.
Consumer continues processing without interruption.

### Experiment 2 - kill the queue master

    # Kill whichever node holds the queue master
    docker compose -f docker-compose.yml stop rabbitmq-1

    # Check from a surviving node - master should have moved
    docker exec distributed_learning-rabbitmq-2-1 rabbitmqctl list_queues name pid slave_pids

Expected: queue master automatically promotes to a mirror on a surviving
node. No messages lost. This is the core value of ha-all mirroring.

### Experiment 3 - observe split-brain with pause

    # Pause node-3 (simulates network freeze not a crash)
    docker pause distributed_learning-rabbitmq-3-1

    # Wait 30 seconds then check
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl cluster_status

    # Unpause and observe recovery
    docker unpause distributed_learning-rabbitmq-3-1
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl list_queues name pid slave_pids

Expected: pausing the master causes nodes 1 and 2 to elect a new master.
When node-3 unpauses it briefly sees itself as master too, visible as two
entries for the same queue in list_queues. This is split-brain made visible.
Recovery requires the isolated node to reset and rejoin.

## Teardown

    docker compose -f docker-compose.yml down -v

The -v flag removes volumes clearing all queue data and cluster state.
Always use -v when reconfiguring to avoid stale Erlang cookie conflicts.

## Troubleshooting

**Cluster formation fails with cookie mismatch:**
Run docker compose -f docker-compose.yml down -v to destroy volumes and restart cleanly.

**Container exits immediately on startup:**
Check logs with docker compose -f docker-compose.yml logs rabbitmq-1.
Most common cause on macOS is file permission error on the cookie file
from a bind mount - resolved by writing the cookie via entrypoint script.

**rabbitmqctl times out:**
The node is still starting. Wait 30 seconds and retry.
