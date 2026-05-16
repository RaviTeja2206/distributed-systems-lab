# Worker - Service Discovery Integration

Python worker demonstrating dynamic service discovery via Consul.
Queries Consul for RabbitMQ endpoints at startup rather than using
hardcoded addresses, with automatic reconnection on node failure.

## The core pattern

Without service discovery - fragile, requires manual updates on node change:

    connection = pika.connect("10.10.0.11:5672")

With Consul service discovery - resilient, automatically finds healthy nodes:

    consul = ConsulClient()
    host, port = consul.get_random_healthy_service("rabbitmq")
    connection = pika.connect(f"{host}:{port}")

When a RabbitMQ node fails the next Consul query returns a different healthy
node. The worker reconnects automatically without any configuration changes.

## Prerequisites

- Consul cluster running (see ../consul-cluster)
- RabbitMQ cluster running (see ../rabbitmq-cluster)
- Python 3.10+ with dependencies:

    pip install pika requests

## Setup

Register RabbitMQ nodes with Consul before starting the worker:

    curl -X PUT http://localhost:8500/v1/catalog/register \
      -H "Content-Type: application/json" \
      -d '{
        "Node": "rabbitmq-node-1",
        "Address": "127.0.0.1",
        "Service": {
          "ID": "rabbitmq-1",
          "Service": "rabbitmq",
          "Address": "127.0.0.1",
          "Port": 5672,
          "Tags": ["messaging", "amqp"]
        }
      }'

## Run

    # Terminal 1 - start the worker
    python3 worker.py

    # Terminal 2 - publish jobs
    python3 producer.py

Worker startup output shows service discovery in action:

    === Service Discovery on Startup ===
    All registered RabbitMQ instances: 1
      127.0.0.1:5672
    Healthy RabbitMQ instances: 1
      127.0.0.1:5672 check
    ====================================
    Querying Consul for RabbitMQ... (attempt 1)
    Consul returned RabbitMQ at 127.0.0.1:5672
    Connected to RabbitMQ at 127.0.0.1:5672
    Waiting for jobs on queue 'lab-jobs'

## File descriptions

**consul_client.py** - reusable service discovery module with two key methods:

    consul = ConsulClient(consul_host="localhost", consul_port=8500)

    # Get all healthy instances
    instances = consul.get_healthy_service("rabbitmq")
    # Returns: [("10.10.0.11", 5672), ("10.10.0.12", 5672)]

    # Get one random healthy instance for basic load balancing
    host, port = consul.get_random_healthy_service("rabbitmq")

Random selection across healthy instances provides basic load balancing.
Production systems use weighted selection or sticky sessions depending
on requirements.

**worker.py** - Consul-aware RabbitMQ consumer with automatic reconnection.

On startup:
1. Queries Consul for all registered RabbitMQ instances
2. Queries for healthy instances only (passing health checks)
3. Selects a random healthy instance
4. Connects via pika with heartbeat and timeout settings
5. Declares queue and starts consuming with prefetch_count=1

On connection failure:
1. Catches AMQPConnectionError or ConnectionClosedByBroker
2. Re-queries Consul for a currently healthy node
3. Reconnects to the new address
4. Resumes consuming from where it left off

**producer.py** - Consul-aware RabbitMQ producer that discovers the broker
address from Consul rather than hardcoding it.

## Key concepts demonstrated

**Service discovery vs hardcoded addresses** - hardcoded IPs break when
nodes restart, scale, or fail. Consul discovery means the worker finds
whatever is currently healthy without any manual reconfiguration.

**Consumer acknowledgment** - workers use basic_ack after successful
processing. If the worker crashes mid-job RabbitMQ re-queues the message
automatically. If processing fails the worker sends basic_nack with
requeue=True to return the message for retry.

**Fair dispatch** - prefetch_count=1 means the worker only receives a new
message after acknowledging the current one. A slow worker gets fewer messages
than a fast one. Without this a single slow worker could receive all messages
while others sit idle.

**Reconnection logic** - the worker catches broker disconnection exceptions
and re-queries Consul rather than retrying the same dead address. This is
the correct pattern - Consul knows which nodes are healthy, the worker
should trust Consul not its own cached address.

**Graceful shutdown** - signal handler catches SIGINT (Ctrl+C) and closes
the connection cleanly before exiting. This allows RabbitMQ to process
the LeaveGroup cleanly rather than timing out the consumer.

## How this maps to the MAM production architecture

In the Evertz MAM broadcast platform every service registers with Consul
on startup. Worker processes and microservices query Consul rather than
using hardcoded addresses.

When a RabbitMQ node fails its health check Consul stops returning it in
discovery responses. Workers that were connected to that node get a
connection error, catch the exception, re-query Consul, and reconnect to
a healthy node. From the application perspective the worker never goes down
- it briefly pauses during reconnection.

The same pattern applies to database connections. Workers query Consul for
a healthy Galera node rather than hardcoding a database IP. When a Galera
node fails workers automatically route to a surviving node.

## Failure simulation

With worker running connect it to a RabbitMQ cluster and stop the node
the worker connected to:

    # In the rabbitmq-cluster directory
    docker compose -f docker-compose.yml stop rabbitmq-1

Watch the worker output:

    Connection lost - re-querying Consul for healthy node
    Querying Consul for RabbitMQ... (attempt 1)
    Consul returned RabbitMQ at 127.0.0.1:5672
    Connected to RabbitMQ at 127.0.0.1:5672
    Waiting for jobs on queue 'lab-jobs'

The worker reconnects automatically. Any unacknowledged messages are
re-queued by RabbitMQ and picked up by the reconnected worker.

## Troubleshooting

**No healthy RabbitMQ instances found:**
Either the RabbitMQ cluster is not running or services are not registered
with Consul. Register services using the curl commands in the Setup section.

**ACCESS_REFUSED - Login was refused:**
Credentials mismatch. Check that the username and password in worker.py
match what the RabbitMQ cluster was configured with.

**Connection refused on 127.0.0.1:5672:**
RabbitMQ is not running or port 5672 is not exposed. Start the RabbitMQ
cluster and verify with: docker compose -f docker-compose.yml ps
