# Distributed Systems Lab

A hands-on learning lab simulating production-grade distributed systems
behavior using Docker Compose. Built to develop genuine operational depth
across the core infrastructure stack used in enterprise media and broadcast
platforms.

## What this is

This lab deploys, operates, and deliberately breaks real distributed systems
to understand how they behave under failure conditions. Every experiment
was run, every failure was observed, and every recovery procedure was
executed manually before being documented here.

This is not a tutorial reproduction. The troubleshooting documentation
reflects real errors encountered and diagnosed during setup and operation.

## Systems covered

| System | Type | Key concepts demonstrated |
|---|---|---|
| RabbitMQ | Message broker | Clustering, HA mirroring, consumer groups, partition handling |
| MariaDB Galera | Database cluster | Active-active replication, quorum enforcement, SST/IST, split-brain recovery |
| Apache Kafka | Event streaming | Partitioning, consumer groups, ISR, leader election, offset management |
| Consul | Service discovery | Raft consensus, health checking, dynamic service registration |
| Ansible | Configuration management | Idempotent playbooks, roles, Jinja2 templates, cluster automation |

## Architecture

All systems are designed around the 3-node quorum pattern — the minimum
required for a cluster to detect failures and remain healthy when one node
goes down. This mirrors the production architecture used in enterprise
broadcast and media asset management platforms.

```text
                ┌─────────────────────────────┐
                │      Consul Cluster         │
                │   Service Discovery + Raft  │
                │   node-1  node-2  node-3    │
                └─────────────┬───────────────┘
                              │ service registration
                ┌─────────────┴──────────────┐
                │                            │
      ┌─────────▼──────────┐    ┌────────────▼────────────┐
      │  RabbitMQ Cluster  │    │     Kafka Cluster       │
      │  HA mirrored       │    │  3 brokers, KRaft mode  │
      │  queues            │    │  3 partitions/topic     │
      └─────────┬──────────┘    └────────────-┬───────────┘
                │                             │
      ┌─────────▼──────────────────────────── ▼────────────┐
      │              Worker Processes                      │
      │   Consul discovery → connect → consume → process   │
      └─────────────────────┬──────────────────────────────┘
                            │
                ┌───────────▼───────────┐
                │   MariaDB Galera      │
                │   Active-active       │
                │   3-node cluster      │
                └───────────────────────┘
```

## Quick start

Each component runs independently. Start with RabbitMQ to understand
the fundamentals, then progress through the stack.

```bash
# RabbitMQ cluster
cd rabbitmq-cluster
docker compose up -d
docker exec distributed_learning-rabbitmq-1-1 rabbitmqctl cluster_status

# Galera cluster
cd galera-cluster
docker compose up -d galera-1
docker compose up -d galera-2 galera-3

# Kafka cluster
cd kafka-cluster
docker compose up -d
docker exec kafka-1 kafka-metadata-quorum \
  --bootstrap-server kafka-1:9092 describe --status

# Consul cluster
cd consul-cluster
docker compose up -d
docker exec consul-cluster-consul-1-1 consul members
```

## Failure experiments

The most valuable learning in this lab comes from deliberately breaking
things. Each component directory contains a documented set of failure
experiments with expected observations.

Key experiments across all systems:

- **Node failure** — kill a cluster member and observe automatic failover
- **Leader election** — kill the active leader and time the new election
- **Quorum loss** — kill majority of nodes and observe write rejection
- **Network partition** — isolate a node and observe split-brain detection
- **Recovery** — restore failed nodes and verify data integrity

## Environment

- macOS with Docker Desktop
- Docker Compose v2.39.4
- Python 3.10+ with kafka-python, pika, requests

## Repository structure

```text
distributed-systems-lab/
├── rabbitmq-cluster/     # 3-node RabbitMQ with HA mirroring
├── galera-cluster/       # 3-node MariaDB Galera active-active
├── kafka-cluster/        # 3-node Kafka KRaft mode
├── consul-cluster/       # 3-node Consul service discovery
├── ansible/              # Automated deployment playbooks
├── worker/               # Python clients with Consul service discovery
└── docs/                 # Detailed experiment documentation
```

## Key learnings

**RabbitMQ vs Kafka** — RabbitMQ is a message queue: messages are deleted
after acknowledgment, one consumer per message. Kafka is a distributed log:
messages are retained, multiple consumer groups read the same data
independently. Understanding when to use each is a core distributed systems
design decision.

**Synchronous vs asynchronous replication** — Galera uses synchronous
certification-based replication: every write is committed on all nodes before
acknowledgment. Kafka uses asynchronous ISR replication with configurable
durability via acks setting. The tradeoff is latency vs availability.

**CP vs AP systems** — Both Galera and Consul are CP systems: they refuse
writes rather than risk inconsistency when quorum is lost. RabbitMQ with
pause_minority is also CP. Understanding the CAP theorem through hands-on
failure injection is qualitatively different from reading about it.
