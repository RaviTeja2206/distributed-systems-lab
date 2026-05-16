#!/bin/bash
# This script starts galera-2 or galera-3 as joining nodes.
# They connect to the existing cluster via wsrep_cluster_address
# and perform SST (State Snapshot Transfer) to get a full copy of the data.

set -e

NODE_NAME=$1

echo "Starting Galera node: $NODE_NAME"

# Write node-specific config
cat >> /etc/mysql/conf.d/galera.cnf << EOF
wsrep_node_address=$NODE_NAME
wsrep_node_name=$NODE_NAME
EOF

exec docker-entrypoint.sh mysqld