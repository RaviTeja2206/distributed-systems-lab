# RabbitMQ Cluster — Troubleshooting Guide

Real errors encountered and resolved during cluster setup and failure
experiments. Every issue here was actually hit, diagnosed, and fixed.

## Environment

- Docker Desktop v2.39.4 (tested on macOS Apple Silicon)
- Docker Engine 27+ (issues and fixes apply to any OS running Docker)
- RabbitMQ image: rabbitmq:3.12-management
- Note: cookie permission issues (Issue 3) are macOS-specific due to
  Docker Desktop bind mount behavior. On Linux hosts this issue does not occur.

---

## Issue 1 — Docker Compose validation error: additional properties not allowed

### Error

    validating docker-compose-cluster.yml: additional properties 'rabbitmq-setup' not allowed

### Root Cause

Two problems combined:
1. The rabbitmq_setup service block was placed outside the services block
   due to incorrect YAML indentation
2. Multiple typos: hyphenated environment variable names (RABBITMQ-ERLANG-COOKIE
   instead of RABBITMQ_ERLANG_COOKIE), wrong key (commands instead of command),
   typo in subnet (10.10.0.0./24), typo in password key (RABBITMQ_DEFAULR_PASS)

### Fix

Move rabbitmq_setup inside the services block with correct indentation.
Fix all environment variable names to use underscores not hyphens.

### Lesson

YAML is whitespace-sensitive. Always validate before running:

    docker compose -f docker-compose.yml config

This parses and validates the file without starting anything. If it prints
the resolved config the file is valid. Make this a habit before every up.

---

## Issue 2 — Erlang cookie mismatch: TCP connection succeeded but Erlang distribution failed

### Error

    TCP connection succeeded but Erlang distribution failed
    suggestion: check if the Erlang cookie is identical for all server nodes
    Erlang cookie hash: 1eDlGwgZ4negYoMCZMXo/w==

### Root Cause

The RABBITMQ_ERLANG_COOKIE environment variable is not reliably picked up
by rabbitmqctl CLI tools. The CLI tool was using a different cookie than
the server nodes, causing Erlang distribution authentication to fail even
though TCP reached the node successfully.

### Fix Attempted (did not work)

Writing the cookie manually in the setup container command:

    echo 'LEARNINGCLUSTERCOOKIE' > /var/lib/rabbitmq/.erlang.cookie
    chmod 400 /var/lib/rabbitmq/.erlang.cookie

This failed because the nodes had already auto-generated their own cookies
on first start and saved them to Docker volumes. The volume-persisted cookie
takes precedence over anything written later.

### Actual Fix

Tear down including volumes so no stale auto-generated cookie survives:

    docker compose -f docker-compose.yml down -v

Then write the cookie inside the container using the entrypoint script.

### Lesson

RabbitMQ generates a random Erlang cookie on first start and persists it
to the data volume. Once saved it takes precedence over environment variables.
Every node and every CLI tool must share the identical cookie for Erlang
distribution to authenticate. Always use down -v when reconfiguring cookie
settings. A plain down without -v leaves stale cookies in volumes.

---

## Issue 3 — Cookie file permission denied (macOS Docker Desktop specific)

### Error

    chown: changing ownership of '/var/lib/rabbitmq/.erlang.cookie': Permission denied

### Root Cause

When mounting a file from macOS into a Docker container Docker Desktop
preserves the macOS file ownership. The mounted cookie file was owned by
root with 400 permissions. The RabbitMQ process runs as user rabbitmq
inside the container and cannot read or change ownership of a root-owned
file so it crashes immediately on startup.

This is a macOS Docker Desktop specific behavior caused by how Docker
Desktop bridges the macOS filesystem into the Linux VM. On Linux hosts
running Docker Engine directly bind mounts behave differently and this
issue does not occur.

### Fix

Stop mounting the cookie as a file. Instead write it inside the container
using a custom entrypoint that runs as root before RabbitMQ starts:

    entrypoint: >
      bash -c "
        echo 'LEARNINGCLUSTERCOOKIE' > /var/lib/rabbitmq/.erlang.cookie &&
        chmod 400 /var/lib/rabbitmq/.erlang.cookie &&
        chown rabbitmq:rabbitmq /var/lib/rabbitmq/.erlang.cookie &&
        exec docker-entrypoint.sh rabbitmq-server
      "

Apply this to all three RabbitMQ nodes and the setup container.

### Lesson

The entrypoint runs as root so it can write and chown the file correctly
before switching to the rabbitmq user. The exec call at the end replaces
the bash process with the RabbitMQ process so signals like SIGTERM on
docker stop are handled correctly by RabbitMQ not by bash.

---

## Issue 4 — Setup container cannot resolve node hostnames: nxdomain

### Error

    unable to connect to epmd (port 4369) on rabbitmq-2: nxdomain (non-existing domain)

### Root Cause

The rabbitmq_setup service had no explicit network assignment in the
compose file. Without being placed on the rabbitmq-net network with a
static IP the setup container could not resolve the hostnames rabbitmq-1,
rabbitmq-2, rabbitmq-3 via Docker internal DNS.

### Fix

Add explicit network assignment with a static IP to the setup container:

    rabbitmq_setup:
      networks:
        rabbitmq-net:
          ipv4_address: 10.10.0.14

### Lesson

Docker Compose internal DNS only resolves service names for containers
that share the same network. Simply listing a network under the service
is not enough when using static IP addressing mode with ipam configuration.
If one container cannot reach another by hostname always check network
membership first.

---

## Issue 5 — RabbitMQ nodes crashing silently on startup

### Symptom

docker ps showed only the setup container running. rabbitmq-1, rabbitmq-2,
and rabbitmq-3 never appeared in docker ps output.

### Diagnosis Command

    docker compose -f docker-compose.yml logs rabbitmq-1

### Error Revealed

    chown: changing ownership of '/var/lib/rabbitmq/.erlang.cookie': Permission denied

### Root Cause

Same as Issue 3. The cookie file mount from macOS was causing containers
to crash immediately. Because they crashed before Docker DNS registered
their hostnames the setup container could not resolve them, producing the
misleading nxdomain error seen in Issue 4.

The underlying cause was always the cookie permission problem. The nxdomain
error was a symptom not the root cause.

### Lesson

When a container fails to appear in docker ps always check its logs
immediately. Containers that crash on startup exit so fast they may not
show in docker ps at all. The actual error is always in the logs not in
network-level errors reported by other containers trying to reach them.

---

## Split-brain observed during pause experiment

### What happened

Pausing the queue master node with docker pause and then unpausing it
caused a brief split-brain condition visible as two entries for the same
queue in rabbitmqctl list_queues output:

    lab-jobs  master: rabbit@rabbitmq-3   slaves: []
    lab-jobs  master: rabbit@rabbitmq-2   slaves: [rabbit@rabbitmq-1]

The cluster also showed a network partition:

    Node rabbit@rabbitmq-1 cannot communicate with rabbit@rabbitmq-3
    Node rabbit@rabbitmq-2 cannot communicate with rabbit@rabbitmq-3

### Why this happened

pause_minority protects against a running node that knows it is isolated.
But a frozen (paused) node cannot evaluate its own minority status while
paused. When it woke up it briefly saw itself as an isolated single node
before the partition was detected and logged.

This is the difference between a clean failure (node crashes, others detect
immediately, election happens cleanly) and a pause/freeze (node suspended
mid-execution, wakes with stale state, brief split-brain before resolution).

### Recovery

    docker compose -f docker-compose.yml down -v
    docker compose -f docker-compose.yml up -d

Or force the authoritative side to reset the isolated node:

    docker exec distributed_learning-rabbitmq-2-1 rabbitmqctl stop_app
    docker exec distributed_learning-rabbitmq-2-1 rabbitmqctl reset
    docker exec distributed_learning-rabbitmq-2-1 rabbitmqctl join_cluster rabbit@rabbitmq-1
    docker exec distributed_learning-rabbitmq-2-1 rabbitmqctl start_app

---

## Quick reference commands

    # Validate compose file without starting
    docker compose -f docker-compose.yml config

    # Check cluster health
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl cluster_status

    # Check queue masters and mirrors
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl list_queues name pid slave_pids

    # Check HA policies
    docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl list_policies

    # Destroy everything including volumes (always use when reconfiguring)
    docker compose -f docker-compose.yml down -v
