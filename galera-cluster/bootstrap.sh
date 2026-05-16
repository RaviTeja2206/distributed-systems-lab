#!/bin/bash
# This script starts galera-1 as the bootstrap node.
# Bootstrap means: "I am starting a new cluster, I am the first node,
# trust my data as the authoritative starting point."
# NEVER bootstrap a node that is not the most up-to-date node.
# NEVER bootstrap if other nodes are already running

set -e

echo "Starting Galera bootstrap node..."

# Write node-specific config
cat >> /etc/mysql/conf.d/galera.cnf << EOF
wsrep_node_address=galera-1
wsrep_node_name=galera-1
EOF

# Bootstrap the cluster - this flag starts a NEW cluster
exec docker-entrypoint.sh mysqld --wsrep-new-cluster