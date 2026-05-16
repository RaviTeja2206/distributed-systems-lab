# Distributed Systems Concepts Reference

A practical reference connecting theoretical distributed systems concepts
to hands-on observations made during lab experiments. Every concept here
was observed directly, not just read about.

---

## CAP Theorem — observed in practice

CAP theorem states that a distributed system can guarantee at most two of:
Consistency, Availability, and Partition tolerance.

In practice partition tolerance is non-negotiable for any network-connected
system. The real choice is between CP and AP.

### CP systems (Consistency + Partition tolerance)

These systems refuse writes rather than risk inconsistency when quorum
is lost. Observed in this lab:

**Galera** — hard-killed 2 of 3 nodes. Surviving node returned immediately:
    ERROR 1047: WSREP has not yet prepared node for application use
    wsrep_cluster_status: non-Primary

**Consul** — stopped 2 of 3 nodes. Write attempt returned immediately:
    No cluster leader

**RabbitMQ with pause_minority** — isolated minority partition paused
itself rather than accepting writes that could diverge from majority.

### AP systems (Availability + Partition tolerance)

These systems continue serving requests even when they cannot guarantee
consistency. RabbitMQ without pause_minority would be an AP system -
it would continue accepting writes on both sides of a partition, risking
message duplication or loss.

### The practical lesson

CP vs AP is not a quality judgment. It is a design decision based on
what failure mode is acceptable for your use case.

A financial transaction database must be CP - duplicate or lost writes
are catastrophic. A content delivery cache can be AP - slightly stale
content is acceptable. A message queue for job processing is often CP -
processing the same job twice causes problems but missing a job is worse.

---

## Quorum — why 3 nodes everywhere

Quorum is the minimum number of nodes that must agree for a cluster to
make progress. For a cluster of N nodes quorum is (N/2) + 1.

    1 node:  quorum = 1  (no fault tolerance)
    2 nodes: quorum = 2  (no fault tolerance - any failure loses quorum)
    3 nodes: quorum = 2  (tolerates 1 failure)
    5 nodes: quorum = 3  (tolerates 2 failures)

This is why every system in this lab uses exactly 3 nodes. Three is the
minimum that provides fault tolerance - you can lose 1 node and the cluster
continues operating with 2 of 3 nodes still forming quorum.

With 2 nodes any single failure loses quorum and the cluster cannot
distinguish between a node failure and a network partition. Both nodes
might think they are the surviving majority.

---

## Raft consensus — how leaders get elected

Raft is the consensus algorithm used by Consul and Kafka KRaft mode.
ZooKeeper uses a similar algorithm called ZAB.

### Leader election process observed in Consul

1. All nodes start in follower state
2. Each follower has a random election timeout (150-300ms typically)
3. The first follower to time out becomes a candidate and requests votes
4. Other nodes vote for the first candidate they hear from
5. A candidate that receives votes from a majority becomes leader
6. The leader sends heartbeats to prevent new elections

When the leader is killed:
1. Followers stop receiving heartbeats
2. First follower to time out starts a new election (new term number)
3. New leader elected within 2-5 seconds typically

Observed timing in lab:
    11:32:57 - heartbeat timeout reached, starting election
    11:32:57 - entering candidate state: term=3
    11:33:01 - New leader elected: consul-3
    Total: 4 seconds from failure to new leader

### Term numbers

Every Raft election increments the term number. Nodes reject messages
from leaders with lower term numbers. This prevents old leaders that
temporarily lost connectivity from interfering after a new leader is
elected. Observed in Kafka as LeaderEpoch incrementing after each
controller election.

---

## Replication — synchronous vs asynchronous

### Synchronous replication (Galera)

Every write must be committed on ALL nodes before acknowledging to client.

    Advantages:
    - Zero replication lag
    - Every node has identical data at all times
    - No stale reads possible on any node

    Disadvantages:
    - Write latency increases with cluster size and network latency
    - Cluster goes read-only if quorum lost
    - Every node must acknowledge every write

Observed: row written on galera-3 immediately readable from galera-1
with zero lag. This is synchronous certification-based replication.

### Asynchronous replication (Kafka with acks=1)

Leader acknowledges write immediately. Followers copy asynchronously.

    Advantages:
    - Lower write latency
    - Leader continues if followers lag
    - Higher throughput

    Disadvantages:
    - Follower may not have latest data if leader fails
    - Data loss possible if leader fails before followers copy

### Configurable durability (Kafka with acks=all)

Producer waits for all in-sync replicas to acknowledge before considering
write successful. Combines higher durability with async follower mechanics.

    acks=0    fire and forget, no acknowledgment
    acks=1    leader acknowledges, followers copy async
    acks=all  all ISR members acknowledge before client gets response

---

## Failure detection — gossip vs heartbeat

### Serf gossip (Consul)

Every node periodically sends heartbeats to a random subset of other nodes.
Failure information propagates through the cluster like gossip - each node
that hears about a failure tells others. Eventually consistent but fast.

Observed: Consul detected kafka-2 failure within 3-4 seconds via gossip
before Raft triggered leader election.

### Raft heartbeat (Consul, Kafka KRaft)

The Raft leader sends periodic heartbeats to all followers. If a follower
does not receive a heartbeat within the election timeout it assumes the
leader is dead and starts an election.

These two mechanisms work together. Gossip detects member failures quickly
and propagates the information. Raft uses its own heartbeat independently
to trigger leader election.

### wsrep inactive timeout (Galera)

Galera uses its own EVS (Extended Virtual Synchrony) protocol for member
failure detection. Nodes exchange keepalive messages. If a node stops
responding within the suspect timeout (default 5 seconds) it is marked
as suspect. After the inactive timeout (default 15 seconds) it is declared
failed and removed from the cluster.

---

## State transfer — how new nodes join

### SST (State Snapshot Transfer) — full copy

Used when a node joins with no existing data or after being out of sync
for too long to use incremental transfer. The donor copies its entire
data directory to the joiner.

    Galera SST methods:
    rsync     - simple, blocks the donor during transfer
    mariabackup - non-blocking, donor continues serving writes

Observed: galera-2 requested SST from galera-1. galera-1 was marked
Donor/Desynced during the transfer (still operational but flagged as busy).
SST completed in under 5 seconds for an empty database.

### IST (Incremental State Transfer) — catch up

Used when a node was a recent cluster member and only missed a small
number of transactions. Transfers only the missed transactions from the
gcache (write-set cache) rather than the full database.

IST is non-blocking and fast. Multiple nodes can do IST simultaneously
without conflict. This is why sequential startup is only required for
fresh clusters with empty volumes.

---

## Consumer patterns — queue vs log

### Message queue pattern (RabbitMQ)

- Message delivered to one consumer
- Message deleted after acknowledgment
- Consumer tracks nothing - broker handles delivery
- Good for: task distribution, RPC, work queues

### Log streaming pattern (Kafka)

- Message written to a persistent log
- Message retained by time or size regardless of consumption
- Consumer tracks its own offset (position in the log)
- Multiple independent consumer groups read the same data
- Good for: event streaming, audit logs, data pipelines, replay

### When to use each

Use RabbitMQ when:
- Each job should be processed by exactly one worker
- Messages should be deleted after processing
- You need complex routing (topic exchanges, header matching)
- Task queue with acknowledgment and retry is the primary pattern

Use Kafka when:
- Multiple systems need to react to the same event
- You need to replay historical events
- Audit logging or compliance requires message retention
- Very high throughput (millions of messages per second)
- Event sourcing or CQRS patterns

---

## Service discovery — why hardcoded IPs fail

In a static deployment with fixed IPs hardcoding works:
    connection = connect("10.10.0.11:5672")

In a dynamic environment this breaks when:
- A node restarts and gets a new IP
- A node fails and traffic should route elsewhere
- You scale up and add new nodes
- You deploy to a new environment with different IPs

Service discovery solves this by introducing a registry:
1. Services register their current address on startup
2. Clients query the registry for healthy instances
3. Registry returns currently available addresses
4. Clients connect to whatever the registry returns

Observed in worker.py:
- Worker queries Consul for healthy RabbitMQ nodes on startup
- Consul returns current healthy addresses
- Worker connects without any hardcoded IPs
- When a node fails its health check Consul stops returning it
- Worker reconnects via Consul and automatically reaches a healthy node

---

## Partition handling strategies

### pause_minority (RabbitMQ)

If a network partition occurs nodes in the minority partition (fewer nodes)
pause and refuse to accept messages. Majority partition continues serving.

Risk: if the minority had the queue master a new master is elected on the
majority side. When partition heals the minority master briefly thinks it
is still master - split-brain for the duration of healing.

### ignore (RabbitMQ alternative)

Both sides of a partition continue accepting messages. When partition heals
Kafka-style: one side wins and the other side's messages are lost.

Highest availability. Highest risk of message loss.

### autoheal (RabbitMQ alternative)

After partition heals the side with more messages wins. The other side
restarts and loses its messages.

### Galera quorum

Galera does not use a configurable partition strategy. Any component
with fewer than half the total cluster nodes automatically becomes
non-Primary and refuses writes. Hard-coded CP behavior.

---

## Ephemeral vs persistent clustering

### Ephemeral (Consul, ZooKeeper)

Service registrations disappear when the service disconnects. This is
correct behavior for service discovery - a crashed service should stop
receiving traffic immediately without any manual deregistration step.

ZooKeeper implements this via ephemeral znodes which are automatically
deleted when the client session ends. Consul implements it via health
checks that mark services critical when they stop responding.

### Persistent (Galera, Kafka)

Cluster membership and data persist across restarts. A node that was
part of the cluster rejoins automatically using its stored cluster
identity and catches up via IST.

This is why docker compose down without -v causes issues - the volumes
retain the old cluster identity and cookie making it hard to form a
fresh cluster.
