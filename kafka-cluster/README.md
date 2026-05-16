# Kafka Cluster

A 3-node Apache Kafka cluster in KRaft mode (no ZooKeeper dependency)
demonstrating partitioned log streaming, consumer groups, ISR replication,
and leader election.

## Architecture

- kafka-1: broker + controller, node.id=1, external port 29092
- kafka-2: broker + controller, node.id=2, external port 29093
- kafka-3: broker + controller, node.id=3, external port 29094

Mode: KRaft (built-in Raft consensus, no ZooKeeper required)
Replication factor: 3 (every partition replicated on every broker)
min.insync.replicas: 2 (writes require 2 replicas to acknowledge)

## Prerequisites

- Docker and Docker Compose
- Python 3.10+ with kafka-python: pip install kafka-python
- Add broker hostnames to /etc/hosts:

    sudo sh -c 'echo "127.0.0.1 kafka-1 kafka-2 kafka-3" >> /etc/hosts'

## Start the cluster

    docker compose -f docker-compose.yml up -d

    # Verify KRaft leader election
    docker exec kafka-1 kafka-metadata-quorum \
      --bootstrap-server kafka-1:9092 describe --status

Expected healthy output:

    LeaderId:        2        (whichever node won the Raft election)
    LeaderEpoch:     1
    MaxFollowerLag:  0        (all brokers fully synchronized)
    CurrentVoters:   [1,2,3]

## Create a topic

    docker exec kafka-1 kafka-topics \
      --bootstrap-server kafka-1:9092 \
      --create \
      --topic media-ingest-jobs \
      --partitions 3 \
      --replication-factor 3

    # Describe topic - shows partition leaders and ISR
    docker exec kafka-1 kafka-topics \
      --bootstrap-server kafka-1:9092 \
      --describe \
      --topic media-ingest-jobs

Sample output:

    Partition: 0  Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
    Partition: 1  Leader: 1  Replicas: 1,2,3  Isr: 1,2,3
    Partition: 2  Leader: 2  Replicas: 2,3,1  Isr: 2,3,1

Each broker leads exactly one partition. Leadership is distributed evenly.
Replicas lists all brokers holding a copy. Isr lists brokers fully in sync.

## Run producer and consumer

    # Terminal 1 - start consumer
    python3 python/consumer.py

    # Terminal 2 - publish messages
    python3 python/producer.py

## Kafka vs RabbitMQ - fundamental difference

RabbitMQ is a message queue. Messages are deleted after acknowledgment.
One consumer gets each message. Good for task queues and RPC.

Kafka is a distributed log. Messages are retained by time or size regardless
of consumption. Multiple consumer groups read the same data independently.
Good for event streaming, audit logs, and data pipelines.

    # This is impossible in RabbitMQ but works in Kafka:
    # Both groups receive every message independently

    # Group 1 - processes jobs
    docker exec -it kafka-1 kafka-console-consumer \
      --bootstrap-server kafka-1:9092 \
      --topic media-ingest-jobs \
      --group transcoding-workers

    # Group 2 - audit log (same messages, independent offset)
    docker exec -it kafka-1 kafka-console-consumer \
      --bootstrap-server kafka-1:9092 \
      --topic media-ingest-jobs \
      --group audit-logger \
      --from-beginning

## Key concepts demonstrated

**Partitioning** - topics split into partitions. Messages with the same key
always route to the same partition via hash. Guarantees ordering per key
while enabling parallel consumption across partitions.

**Consumer groups** - consumers in a group share partition assignments.
Each partition consumed by exactly one group member at a time. Adding
consumers triggers a rebalance redistributing partitions. Multiple
independent groups read the same topic simultaneously.

**ISR (In-Sync Replicas)** - replicas fully caught up with the partition
leader. min.insync.replicas=2 means producers with acks=all require 2 ISR
members to acknowledge. If ISR drops to 1 writes are rejected. Same CP
behavior as Galera.

**KRaft vs ZooKeeper** - traditional Kafka used ZooKeeper for controller
election. KRaft replaces this with built-in Raft consensus stored in the
__cluster_metadata topic. No external dependency needed. This lab uses
KRaft mode reflecting current production practice.

**Preferred leader rebalancing** - each partition has a preferred leader
(first broker in Replicas list). After a broker failure and recovery Kafka
automatically restores preferred leadership within 5 minutes by default,
rebalancing load across brokers.

**Key-based routing** - producer keys determine partition assignment.
asset001 always routes to the same partition regardless of when it is
published. All events for the same asset arrive in order at the consumer.

## Failure experiments

### Experiment 1 - observe partition routing and ordering

    docker exec -it kafka-1 kafka-console-consumer \
      --bootstrap-server kafka-1:9092 \
      --topic media-ingest-jobs \
      --from-beginning \
      --property print.key=true \
      --property print.partition=true \
      --property print.offset=true

Expected: messages with the same key always appear in the same partition.
Within each partition offsets increment sequentially. Across partitions
there is no ordering guarantee.

### Experiment 2 - consumer group partition assignment

    # Start 3 consumers in same group in separate terminals
    docker exec -it kafka-1 kafka-console-consumer \
      --bootstrap-server kafka-1:9092 \
      --topic media-ingest-jobs \
      --group transcoding-workers \
      --property print.partition=true

    # Check assignment after all 3 consumers join
    docker exec kafka-1 kafka-consumer-groups \
      --bootstrap-server kafka-1:9092 \
      --describe \
      --group transcoding-workers

Expected: each partition assigned to exactly one consumer. Each message
appears in only one terminal. This is the competing consumer pattern.

### Experiment 3 - broker failure and ISR recovery

    # Note current partition leaders
    docker exec kafka-1 kafka-topics \
      --bootstrap-server kafka-1:9092 \
      --describe --topic media-ingest-jobs

    # Kill the active controller
    docker compose -f docker-compose.yml stop kafka-2

    # Observe leader election and ISR update
    docker exec kafka-1 kafka-topics \
      --bootstrap-server kafka-1:9092 \
      --describe --topic media-ingest-jobs

    # Publish while broker is down - should succeed (2 of 3 ISR remaining)
    docker exec -it kafka-1 kafka-console-producer \
      --bootstrap-server kafka-1:9092 \
      --topic media-ingest-jobs \
      --property key.separator=: \
      --property parse.key=true

    # Restore broker
    docker compose -f docker-compose.yml start kafka-2

Expected: partition leadership moves to a surviving broker within seconds.
kafka-2 removed from ISR. Writes continue because min.insync.replicas=2
is satisfied. After kafka-2 restarts it rejoins ISR. Preferred leader
rebalancing eventually restores original leadership distribution.

### Experiment 4 - independent consumer groups

    # Start audit-logger group reading from the beginning
    docker exec -it kafka-1 kafka-console-consumer \
      --bootstrap-server kafka-1:9092 \
      --topic media-ingest-jobs \
      --group audit-logger \
      --from-beginning

Expected: audit-logger reads ALL messages from offset 0 including those
already consumed by transcoding-workers. New messages appear in both groups
simultaneously. Each group maintains its own independent offset.

## Teardown

    docker compose -f docker-compose.yml down -v

## Troubleshooting

**DNS lookup failed for kafka-1:9092:**
The Kafka client connects via bootstrap servers then receives advertised
listener hostnames. Add to /etc/hosts:
sudo sh -c 'echo "127.0.0.1 kafka-1 kafka-2 kafka-3" >> /etc/hosts'

**NotCoordinatorError on consumer startup:**
Stale consumer group metadata from a previous session. Use a new group_id
in consumer.py or wait for the coordinator to time out the old session.

**Connection refused on port 29092:**
The external listener is not bound. Verify the compose file has
EXTERNAL://0.0.0.0:29092 in KAFKA_LISTENERS and the port mapping
- 29092:29092 in the ports section.

**MaxFollowerLag > 0 in metadata quorum:**
A broker is catching up after a restart. Wait 30 seconds and recheck.
MaxFollowerLag of 0 means all brokers are fully synchronized.
