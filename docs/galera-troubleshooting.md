# MariaDB Galera Cluster — Troubleshooting Guide

Real errors encountered and resolved during cluster setup and failure
experiments. Every issue here was actually hit, diagnosed, and fixed.

## Environment

- Docker Desktop v2.39.4 (tested on macOS Apple Silicon)
- Docker Engine 27+ (issues and fixes apply to any OS running Docker)
- MariaDB image: mariadb:10.11
- Galera provider: galera-4 (libgalera_smm.so version 26.4.25)

---

## Issue 1 — Wrong Galera provider library path

### Error

    WSREP: wsrep_load(): dlopen(): /user/lib/galera/libgalera_smm.so:
    cannot open shared object file: No such file or directory
    WSREP: Failed to load provider
    Aborting

### Root Cause

Typo in galera.cnf - /user/lib/ instead of /usr/lib/. A single missing
character caused the entire node to abort on startup.

### Fix

    # Wrong
    wsrep_provider=/user/lib/galera/libgalera_smm.so

    # Correct
    wsrep_provider=/usr/lib/galera/libgalera_smm.so

### Lesson

Verify the exact library path inside the container before writing config:

    docker run --rm mariadb:10.11 find / -name "libgalera*" 2>/dev/null

This command shows the correct path for any image version without guessing.

---

## Issue 2 — SST conflict: two nodes requesting state transfer simultaneously

### Error

    State transfer to 0.0 (galera-2) failed: No message of desired type
    Will never receive state. Need to abort.
    mysqld: Terminated.

### Root Cause

galera-2 and galera-3 both started simultaneously with empty volumes and
both requested a full SST (State Snapshot Transfer) from galera-1 at the
same time. A donor can only serve one SST at a time. The second request
was aborted.

This happened because depends_on in Docker Compose only checks if a
container has started not if the application inside is ready and accepting
connections. Both nodes started within milliseconds of each other.

### Fix

Replace simple depends_on with depends_on using condition: service_healthy
combined with a MariaDB healthcheck. This enforces true sequential startup:

    galera-2:
      depends_on:
        galera-1:
          condition: service_healthy

    galera-3:
      depends_on:
        galera-2:
          condition: service_healthy

    healthcheck:
      test: ["CMD", "mariadb", "-uroot", "-plabpassword",
             "-e", "SHOW STATUS LIKE 'wsrep_local_state_comment'"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 30s

This creates the chain: galera-1 healthy then galera-2 starts then
galera-2 healthy then galera-3 starts. Each node completes SST before
the next begins.

### Lesson

SST conflicts only occur when two nodes with empty volumes join simultaneously.
Once nodes have existing data they use IST (Incremental State Transfer)
which is fast, non-blocking, and safe to run simultaneously. The sequential
startup requirement only applies to fresh cluster initialization with
empty volumes.

---

## Issue 3 — Post hard-kill recovery deadlock: cluster stuck in non-Primary

### Error

    New COMPONENT: primary = no, bootstrap = no
    Received NON-PRIMARY
    wsrep_cluster_status: non-Primary

### Root Cause

Two nodes were hard-killed with docker kill simultaneously. When they
restarted they had inconsistent state files from the abrupt termination.
galera-3 also failed to start because of the healthcheck dependency chain
- it was blocked waiting for galera-2 to become healthy but galera-2
could not become healthy without galera-3 to form quorum. Classic deadlock.

### Difference between graceful and hard failure

docker compose stop sends SIGTERM. The node says goodbye to the cluster.
The cluster recalculates quorum. The surviving node can become a legitimate
single-node Primary.

docker kill sends SIGKILL. The node vanishes instantly. The surviving node
cannot determine if it has quorum so it goes non-Primary immediately.

### Fix

Force the most up-to-date surviving node to become the Primary component:

    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SET GLOBAL wsrep_provider_options='pc.bootstrap=YES';"

This tells galera-1 to declare itself the authoritative Primary component
and stop waiting for the others. Once galera-1 becomes Primary galera-2
automatically rejoins. Then start galera-3 manually:

    docker compose -f docker-compose.yml start galera-3

### When to use pc.bootstrap=YES

Only use this command on the node with the highest wsrep_last_committed
sequence number - the most up-to-date node. Using it on a stale node
risks data loss. Find the most up-to-date node by checking each survivor:

    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SHOW STATUS LIKE 'wsrep_last_committed';"

### Lesson

pc.bootstrap=YES is a critical production DBA recovery command for Galera
clusters that cannot reform quorum after simultaneous multi-node failure.
It is the correct fix - not restarting containers repeatedly hoping quorum
reforms automatically.

---

## Issue 4 — Healthcheck dependency blocking restart after hard kill

### Error

    dependency failed to start: container galera-cluster-galera-2-1 is unhealthy

### Root Cause

After a hard kill the healthcheck start_period of 30 seconds was too short
for galera-2 to recover its state from disk and rejoin the cluster. The
healthcheck declared failure before the node was ready causing galera-3
to be blocked from starting at all.

### Fix

Increase start_period and retries to give nodes more time to recover:

    healthcheck:
      test: ["CMD", "mariadb", "-uroot", "-plabpassword",
             "-e", "SHOW STATUS LIKE 'wsrep_local_state_comment'"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 60s

This gives nodes up to 5 minutes total to recover before the healthcheck
declares failure. More realistic for nodes recovering from a hard kill.

---

## CP behavior verified — quorum loss rejects writes immediately

### Observed

After hard-killing two nodes and attempting a write on the surviving node:

    ERROR 1047 (08S01): WSREP has not yet prepared node for application use

Status on the surviving node:

    wsrep_cluster_size         | 1
    wsrep_cluster_status       | non-Primary
    wsrep_local_state_comment  | Initialized

### Why this is correct behavior

Galera is a CP system. With only 1 of 3 nodes reachable it cannot confirm
whether the other 2 nodes crashed or whether it is the isolated minority.
Rather than risk accepting writes that cannot be replicated it refuses all
writes immediately.

This is different from a graceful shutdown where nodes say goodbye and the
cluster correctly identifies the survivor as a legitimate single-node
Primary. Hard kills create ambiguity. Galera resolves ambiguity by going
read-only.

### Data integrity confirmed

After full cluster recovery:

    SELECT * FROM jobs;

All rows written before the hard kill were present. The row rejected during
non-Primary state was not present. Galera never lost committed data and
never accepted data it could not replicate.

---

## Quick reference commands

    # Check full cluster health
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SHOW STATUS LIKE 'wsrep_cluster%';"

    # Check this node's replication state
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SHOW STATUS LIKE 'wsrep_local_state_comment';"

    # Find most up-to-date node after crash
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SHOW STATUS LIKE 'wsrep_last_committed';"

    # Force primary component on most up-to-date node
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SET GLOBAL wsrep_provider_options='pc.bootstrap=YES';"

    # Check grastate.dat for safe_to_bootstrap flag
    docker exec galera-cluster-galera-1-1 cat /var/lib/mysql/grastate.dat

    # Destroy everything including volumes (always use when reconfiguring)
    docker compose -f docker-compose.yml down -v

---

## wsrep state reference

    wsrep_cluster_status = Primary
      Node has quorum and accepts reads and writes. Healthy state.

    wsrep_cluster_status = non-Primary
      Node lost quorum. Reads may work from local state. Writes rejected.

    wsrep_local_state_comment = Synced
      Node fully caught up with the cluster. Normal operating state.

    wsrep_local_state_comment = Donor/Desynced
      Node is serving SST to a joining node. Still operational but marked
      as busy. Reads may return slightly stale data during donor phase.

    wsrep_local_state_comment = Joiner
      Node is receiving SST from a donor. Not yet operational.

    wsrep_local_state_comment = Initialized
      Node is running but not participating in replication. Write rejected.
      Occurs after quorum loss or before cluster membership is established.
