# Kafka Cluster — Troubleshooting Guide

Real errors encountered and resolved during cluster setup and failure
experiments. Every issue here was actually hit, diagnosed, and fixed.

## Environment

- Docker Desktop v2.39.4 (tested on macOS Apple Silicon)
- Docker Engine 27+ (issues and fixes apply to any OS running Docker)
- Kafka image: confluentinc/cp-kafka:7.5.0
- Python client: kafka-python 2.3.1

---

## Issue 1 — DNS lookup failed for kafka-1:9092

### Error

    DNS lookup failed for kafka-1:9092, exception was [Errno 8] nodename
    nor servname provided, or not known
    KafkaConnectionError: DNS failure

### Root Cause

The Python client connects to localhost:9092 successfully as the bootstrap
server. Kafka then returns its advertised listener hostnames (kafka-1:9092,
kafka-2:9092, kafka-3:9092) as the addresses to use for all subsequent
connections. The Mac cannot resolve these Docker internal hostnames because
they only exist inside the Docker network.

This is the advertised listeners problem - a very common Kafka setup issue.
The bootstrap address and the advertised address are two different things.

### Fix

Add the Kafka broker hostnames to /etc/hosts so they resolve to localhost:

    sudo sh -c 'echo "127.0.0.1 kafka-1 kafka-2 kafka-3" >> /etc/hosts'

Verify it was added:

    cat /etc/hosts | grep kafka

### Why this works

With this entry kafka-1 resolves to 127.0.0.1. Docker port mappings then
route 127.0.0.1:29092 into the kafka-1 container. The client connects
using the hostname Kafka advertised and everything lines up correctly.

### Lesson

In production on AWS the fix is setting KAFKA_ADVERTISED_LISTENERS to
the public IP or DNS name of each broker. In a Docker lab the fix is
/etc/hosts. The root cause is identical - Kafka tells clients to connect
to the advertised listener address not the address the client originally
used for bootstrap.

---

## Issue 2 — NotCoordinatorError on consumer group join

### Error

    NotCoordinatorError: [Error 16] NotCoordinatorError
    Attempt to join group failed due to obsolete coordinator information
    Marking the coordinator dead for group transcoding-workers

### Root Cause

Stale consumer group coordinator information from a previous session.
When kafka-2 (which was the group coordinator) went down and came back
up the consumer group metadata was in an inconsistent state. The client
kept finding and losing the coordinator in a loop.

### Fix

Use a fresh consumer group name to avoid stale metadata:

    GROUP_ID = 'transcoding-workers-py'

Or wait for the coordinator session to time out (up to 30 seconds) before
reconnecting with the same group ID.

### Lesson

Consumer group state is stored in Kafka's internal __consumer_offsets topic.
After broker failures this metadata can become temporarily inconsistent
while the cluster recovers. Using a new group ID bypasses the stale state
entirely. In production use a unique group ID per deployment or application
version to avoid cross-version coordination conflicts.

---

## Issue 3 — Connection refused on external listener port 29092

### Error

    Connect attempt returned error 61. Disconnecting.
    KafkaConnectionError: 61 ECONNREFUSED

### Root Cause

The external listener port was not correctly bound because of a
misconfiguration in the compose file. The kafka-1 service had wrong
port mappings that conflicted with the controller port, and the
EXTERNAL listener was not properly configured.

The compose file had:
    ports:
      - "9092:9092"
      - "9093:9093"   # conflicted with controller internal port

Instead of:
    ports:
      - "29092:29092"  # dedicated external listener port

### Fix

Use dedicated non-conflicting ports for external listeners. The correct
pattern separates three types of ports:

    PLAINTEXT://0.0.0.0:9092    # broker-to-broker internal traffic
    CONTROLLER://0.0.0.0:19092  # KRaft controller Raft traffic
    EXTERNAL://0.0.0.0:29092    # client traffic from outside Docker

Each broker gets its own external port:
    kafka-1: 29092
    kafka-2: 29093
    kafka-3: 29094

And advertises the correct external address:
    KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka-1:9092,EXTERNAL://localhost:29092

### Verify ports are bound

    lsof -i :29092
    lsof -i :29093
    lsof -i :29094

All three should show a Docker process listening. If any is missing the
compose file has a port mapping error for that broker.

### Lesson

Kafka listener configuration is one of the most common sources of confusion.
Three concepts must align for external clients to connect correctly:

    KAFKA_LISTENERS          - what the broker binds to (0.0.0.0 = all interfaces)
    KAFKA_ADVERTISED_LISTENERS - what the broker tells clients to connect to
    ports in compose file    - what Docker exposes from the container

If any of these three are misaligned clients either cannot connect at all
or connect to the bootstrap but fail on subsequent broker connections.

---

## Issue 4 — Kafka compose file not updating despite edits

### Symptom

After editing docker-compose-kafka.yml and running docker compose up -d
the containers started with old configuration. Port 29092 was still not
exposed even though the file showed the correct mapping.

### Root Cause

The file was not actually saved correctly. The cat heredoc command failed
silently because of comment lines (lines starting with #) being interpreted
as shell commands in zsh. The file on disk still had the old content.

### Diagnosis

    head -20 docker-compose-kafka.yml

If line 16 showed - "9092:9092" instead of - "29092:29092" the file
was not updated.

### Fix

Use Python to write the file instead of bash heredoc to avoid shell
interpretation issues with special characters:

    python3 -c "
    content = '''...file content...'''
    with open('docker-compose-kafka.yml', 'w') as f:
        f.write(content)
    "

Always verify the file was written correctly before running docker compose:

    head -20 docker-compose-kafka.yml
    grep "29092" docker-compose-kafka.yml

### Lesson

In zsh lines starting with # inside a heredoc can be interpreted as
commands causing the heredoc to fail or produce unexpected results.
Python file writing is more reliable for complex multi-line content.
Always verify file contents before running commands that depend on them.

---

## Issue 5 — MaxFollowerLag non-zero after broker restart

### Observed

After restarting kafka-2 following a docker compose stop:

    docker exec kafka-1 kafka-metadata-quorum \
      --bootstrap-server kafka-1:9092 describe --status

    MaxFollowerLag:     3078
    MaxFollowerLagTimeMs: -1

### Root Cause

kafka-2 was still catching up on metadata records written while it was
offline. MaxFollowerLagTimeMs of -1 means the lagging node is unreachable
or still initializing. The large lag number represents the number of
metadata log entries the follower needs to replay.

### Resolution

Wait 15-30 seconds and recheck. Once kafka-2 fully rejoins:

    MaxFollowerLag:     0
    MaxFollowerLagTimeMs: 0

MaxFollowerLag of 0 means all brokers are fully synchronized with the
controller leader. This is the healthy state.

### Lesson

MaxFollowerLag in kafka-metadata-quorum output is the Kafka equivalent
of wsrep_cluster_size in Galera. It tells you at a glance whether the
cluster is fully healthy. Always check this after any broker restart
before running experiments or benchmarks.

---

## Partition leader behavior after broker recovery

### Observed

After killing kafka-2 (the active controller and partition 2 leader)
kafka-3 was elected as the new partition 2 leader. After kafka-2
restarted it initially rejoined as a follower. After approximately
5 minutes kafka-2 reclaimed partition 2 leadership.

### Why leadership did not return immediately

When kafka-2 restarted it needed to catch up on all messages written
while it was offline before it could safely lead. Promoting it to leader
immediately while catching up would risk stale reads.

Kafka waits until kafka-2 is fully in the ISR (confirmed caught up)
before considering it for leadership rebalancing.

### Preferred leader rebalancing

Kafka runs auto.leader.rebalance on a configurable interval
(leader.imbalance.check.interval.seconds, default 300 seconds).
When this fires it moves partition leadership back to the preferred
leader (first broker in the Replicas list) if it has returned and
is fully in-sync.

### Why controller leader did not return to kafka-2

The KRaft controller leader (shown in kafka-metadata-quorum as LeaderId)
does not automatically rebalance. kafka-1 won the controller election at
epoch 2 when kafka-2 went down and kept that role after kafka-2 returned.
The controller stays where it is until it fails. This is expected and
correct behavior.

---

## Quick reference commands

    # Check KRaft cluster health
    docker exec kafka-1 kafka-metadata-quorum \
      --bootstrap-server kafka-1:9092 describe --status

    # List all brokers
    docker exec kafka-1 kafka-broker-api-versions \
      --bootstrap-server kafka-1:9092

    # Describe topic partition leaders and ISR
    docker exec kafka-1 kafka-topics \
      --bootstrap-server kafka-1:9092 \
      --describe --topic media-ingest-jobs

    # Check consumer group assignment and lag
    docker exec kafka-1 kafka-consumer-groups \
      --bootstrap-server kafka-1:9092 \
      --describe --group transcoding-workers

    # Verify external ports are listening
    lsof -i :29092 && lsof -i :29093 && lsof -i :29094

    # Destroy everything including volumes
    docker compose -f docker-compose.yml down -v

---

## Kafka listener configuration reference

    Listener type    Purpose                          Port convention
    PLAINTEXT        Broker-to-broker internal        9092 (internal only)
    CONTROLLER       KRaft Raft consensus traffic     19092 (internal only)
    EXTERNAL         Client connections from outside  29092/29093/29094

    KAFKA_LISTENERS defines what the broker binds to.
    KAFKA_ADVERTISED_LISTENERS defines what clients are told to use.
    These must be consistent or clients connect to bootstrap but fail
    on all subsequent broker connections.
