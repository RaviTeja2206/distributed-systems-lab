# MariaDB Galera Cluster

A 3-node MariaDB Galera active-active cluster demonstrating synchronous
database replication, quorum enforcement, and split-brain recovery.

## Architecture

- galera-1: bootstrap node, primary on first start
- galera-2: joins via SST from galera-1
- galera-3: joins via SST after galera-2 is synced

Replication: synchronous certification-based (wsrep)
SST method: rsync
Quorum: 3-node, tolerates 1 failure
Min ISR: 2 nodes must acknowledge every write

## Prerequisites

- Docker and Docker Compose

## Start the cluster

Order matters. galera-1 must bootstrap first. The compose file enforces
sequential startup automatically via healthcheck dependencies.

    docker compose -f docker-compose.yml up -d

    # Verify all 3 nodes joined
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SHOW STATUS LIKE 'wsrep_cluster%';"

Expected healthy output:

    wsrep_cluster_size    | 3
    wsrep_cluster_status  | Primary

## Key status variables

    -- Full cluster health check
    SHOW STATUS LIKE 'wsrep_cluster%';
    SHOW STATUS LIKE 'wsrep_local_state_comment';

    -- wsrep_cluster_status = Primary means node has quorum
    -- wsrep_cluster_status = non-Primary means quorum lost, writes rejected
    -- wsrep_local_state_comment = Synced means fully caught up
    -- wsrep_local_state_comment = Donor means serving SST to joining node

## Key concepts demonstrated

**Active-active replication** - every node accepts reads and writes.
Galera uses synchronous certification to ensure all writes are committed
on all nodes before acknowledging to the client. No replication lag.

**SST vs IST** - State Snapshot Transfer (SST) copies the full database
to a new joining node. Incremental State Transfer (IST) sends only missed
transactions to a recovering node. SST blocks the donor node. IST does not.
Sequential startup prevents two nodes requesting SST simultaneously which
causes the second node to abort with: Will never receive state. Need to abort.

**CP behavior** - Galera is a CP system. When quorum is lost it refuses
writes immediately rather than risk divergence. Demonstrated by hard-killing
two nodes and observing ERROR 1047 on the surviving node.

**Bootstrap order** - a fresh cluster requires exactly one node to start
with --wsrep-new-cluster. Others join it. Wrong order causes a three-way
deadlock where all nodes wait for each other indefinitely.

**Graceful vs hard failure** - docker compose stop sends SIGTERM so nodes
say goodbye and the cluster recalculates quorum cleanly. docker kill sends
SIGKILL so nodes vanish without warning and the survivor cannot determine
if it has quorum, triggering non-Primary state.

## Failure experiments

### Experiment 1 - active-active write verification

    # Connect to galera-1 and create data
    docker exec -it galera-cluster-galera-1-1 mariadb -uroot -plabpassword labdb

    CREATE TABLE jobs (
      id INT AUTO_INCREMENT PRIMARY KEY,
      job_name VARCHAR(100),
      status VARCHAR(20)
    );
    INSERT INTO jobs (job_name, status) VALUES ('render_001', 'queued');
    exit

    # Read from galera-3 - should see the row immediately with zero lag
    docker exec galera-cluster-galera-3-1 mariadb -uroot -plabpassword labdb \
      -e "SELECT * FROM jobs;"

Expected: row written on galera-1 appears on galera-3 instantly.
This proves synchronous replication - no lag, no eventual consistency.

### Experiment 2 - quorum loss (CP behavior)

    # Hard-kill two nodes simultaneously
    docker kill galera-cluster-galera-2-1 galera-cluster-galera-3-1

    # Try to write on the surviving node
    docker exec -it galera-cluster-galera-1-1 mariadb -uroot -plabpassword labdb

    INSERT INTO jobs (job_name, status) VALUES ('should_fail', 'testing');

Expected: ERROR 1047: WSREP has not yet prepared node for application use

The node immediately refuses writes. wsrep_cluster_status shows non-Primary.
This is Galera CP guarantee - consistency is never sacrificed.

    SHOW STATUS LIKE 'wsrep_cluster_status';
    -- Returns: non-Primary

    SHOW STATUS LIKE 'wsrep_local_state_comment';
    -- Returns: Initialized

### Experiment 3 - recovery after hard multi-node failure

    # Restart the killed nodes
    docker compose -f docker-compose.yml start galera-2 galera-3

    # If cluster cannot reform quorum automatically force the primary component
    # Only run this on the node with the highest wsrep_last_committed value
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SET GLOBAL wsrep_provider_options='pc.bootstrap=YES';"

    # Verify full recovery
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword \
      -e "SHOW STATUS LIKE 'wsrep_cluster%';"

    # Confirm data integrity - rejected writes should not appear
    docker exec galera-cluster-galera-1-1 mariadb -uroot -plabpassword labdb \
      -e "SELECT * FROM jobs;"

pc.bootstrap=YES is the critical production DBA recovery command for Galera
clusters that cannot reform quorum after simultaneous multi-node failure.
Only use it on the most up-to-date node.

## Teardown

    docker compose -f docker-compose.yml down -v

## Troubleshooting

**All nodes show safe_to_bootstrap: 0 after crash:**
Check wsrep_last_committed on each node. The node with the highest value
should have safe_to_bootstrap set to 1 in /var/lib/mysql/grastate.dat
before restarting that node first.

**SST conflict - node aborts with Will never receive state:**
Two nodes requested SST simultaneously. Run docker compose -f docker-compose.yml down -v
and restart. The healthcheck chain enforces sequential startup automatically.

**Cluster size shows 2 after restart:**
One node is still joining via SST. Wait 30 seconds and recheck.
If still 2 check that node logs for SST errors.

**wsrep_local_state_comment shows Donor/Desynced:**
A node is currently serving SST to a joining node. This is normal.
The donor resumes normal operation after SST completes.
