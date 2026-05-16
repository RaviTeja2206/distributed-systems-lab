# Monitoring — Prometheus and Grafana

A monitoring stack that collects metrics from the Kafka cluster and
visualizes them in Grafana dashboards. Demonstrates the pull-based
Prometheus scraping model, JMX metric exposure, and real-time failure
observation.

## Architecture

    Kafka brokers (kafka-1, kafka-2, kafka-3)
        |
        | JMX Prometheus Java Agent (ports 9101, 9102, 9103)
        | exposes JVM and Kafka internal metrics
        |
    kafka-exporter (port 9308)
        | polls Kafka brokers every 30 seconds
        | exposes topic, partition, consumer group metrics
        |
    Prometheus (port 9090)
        | scrapes JMX endpoints every 15 seconds
        | scrapes kafka-exporter every 15 seconds
        | stores time-series data
        |
    Grafana (port 3000)
        | queries Prometheus via PromQL
        | renders dashboards and alerts

## Two metric sources

**JMX Prometheus Java Agent** - runs inside each Kafka broker JVM.
Exposes internal broker metrics: request rates, replica lag, log sizes,
network processor idle percent, JVM heap usage. These are the deep
internals of each broker.

**kafka-exporter** - runs as a separate container. Connects to Kafka
brokers as a client and exposes cluster-level metrics: broker count,
topic partition counts, consumer group lag per partition. These are
the operational metrics you care about day to day.

## Prerequisites

- Docker and Docker Compose
- JMX Prometheus Java Agent jar (binary not committed to repo)

Download the jar before starting:

    curl -Lo jmx/jmx_prometheus_javaagent.jar \
      https://repo1.maven.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/0.19.0/jmx_prometheus_javaagent-0.19.0.jar

## Start the monitoring stack

    docker compose -f docker-compose-monitoring.yml up -d

    # Verify all services running
    docker compose -f docker-compose-monitoring.yml ps

Services started:
- kafka-1, kafka-2, kafka-3 (with JMX agent)
- kafka-exporter
- prometheus
- grafana
- kafka-ui

## Verify metrics are flowing

Check Prometheus targets - all should show UP:

    http://localhost:9090/targets

Expected:
    kafka-jmx     3/3 up   (kafka-1:9101, kafka-2:9102, kafka-3:9103)
    kafka-exporter 1/1 up   (kafka-exporter:9308)

Verify JMX metrics directly:

    curl http://localhost:9101/metrics | grep "^kafka_" | head -20

Verify kafka-exporter metrics:

    curl http://localhost:9308/metrics | grep "^kafka_"

## Configure Grafana

Open Grafana at http://localhost:3000
Default credentials: admin / admin

Add Prometheus data source:
    Connections -> Data Sources -> Add new data source -> Prometheus
    URL: http://prometheus:9090
    Click Save and Test

Create dashboard - Dashboards -> New -> New Dashboard -> Add panel

Key panels to build:

    Panel: Active Brokers
    Query: kafka_brokers
    Visualization: Stat

    Panel: Topic Partitions
    Query: kafka_topic_partitions
    Visualization: Bar chart or Stat

    Panel: Consumer Group Lag
    Query: kafka_consumergroup_lag
    Visualization: Time series

    Panel: JVM Heap Usage per Broker
    Query: jvm_memory_bytes_used{area="heap"}
    Visualization: Time series

## Create test data

CLI commands inside Kafka containers require KAFKA_OPTS to be cleared
to prevent the JMX agent from attempting to bind to an already-used port:

    # Create topic
    docker exec -e KAFKA_OPTS="" kafka-1 kafka-topics \
      --bootstrap-server kafka-1:9092 \
      --create --topic monitoring-test \
      --partitions 3 --replication-factor 3

    # Produce messages
    for i in $(seq 1 20); do
      echo "message-$i" | docker exec -e KAFKA_OPTS="" -i kafka-1 \
        kafka-console-producer \
        --bootstrap-server kafka-1:9092 \
        --topic monitoring-test
    done

    # Consume to create consumer group lag metrics
    docker exec -e KAFKA_OPTS="" -it kafka-1 kafka-console-consumer \
      --bootstrap-server kafka-1:9092 \
      --topic monitoring-test \
      --group monitoring-group \
      --from-beginning \
      --max-messages 20

## Failure experiment — observe broker failure in Grafana

Open Grafana dashboard. Set time range to Last 15 minutes and
refresh interval to 10s.

Kill a broker:

    docker compose -f docker-compose-monitoring.yml stop kafka-2

Watch the Active Brokers panel drop from 3 to 2.

Restore the broker:

    docker compose -f docker-compose-monitoring.yml start kafka-2

Watch the Active Brokers panel recover to 3.

## Why there is a monitoring delay

The delay between a broker failing and the dashboard updating is expected.
Prometheus is a pull-based system with three layers of latency:

    kafka-exporter polls brokers:  every 30 seconds
    Prometheus scrapes exporter:   every 15 seconds (scrape_interval)
    Grafana refreshes dashboard:   every 5-10 seconds

Worst case total delay: approximately 50 seconds from failure to dashboard update.

To reduce detection latency decrease scrape_interval in prometheus.yml.
The tradeoff is higher load on the monitored system and more storage
consumed by Prometheus. Production systems typically use 15-30 second
scrape intervals with alerting rules that fire faster than dashboard
refresh cycles.

## Key metrics reference

From kafka-exporter (port 9308):

    kafka_brokers
      Number of brokers currently reachable. Drop indicates broker failure.

    kafka_topic_partitions{topic="..."}
      Number of partitions for a topic.

    kafka_consumergroup_lag{consumergroup="...", partition="...", topic="..."}
      Messages in topic not yet consumed by the group. Non-zero and growing
      means consumers are falling behind producers.

    kafka_broker_info{address="...", id="..."}
      Metadata about each broker. Disappears when broker is unreachable.

From JMX agent (ports 9101/9102/9103):

    jvm_memory_bytes_used{area="heap"}
      JVM heap memory used per broker. Sustained high heap usage
      indicates GC pressure or memory leak.

    kafka_server_replicamanager_underreplicatedpartitions
      Partitions with fewer than the configured replication factor
      in sync. Non-zero means a broker is behind or down.

    kafka_network_socketserver_networkprocessoravgidlepercent
      Network thread idle percentage. Low values indicate the broker
      is saturated with network requests.

## Troubleshooting

**Prometheus targets show DOWN for kafka-jmx:**
The JMX agent jar is not loading. Verify the jar exists at the correct path:
    ls -lh jmx/jmx_prometheus_javaagent.jar
Check broker logs for agent load errors:
    docker compose -f docker-compose-monitoring.yml logs kafka-1 | grep -i "jmx\|agent"

**CLI commands fail with Address already in use:**
KAFKA_OPTS is being inherited by the CLI tool causing the JMX agent
to try binding to an already-used port. Always use:
    docker exec -e KAFKA_OPTS="" kafka-1 kafka-topics ...

**Grafana shows No data for a panel:**
Verify the metric exists in Prometheus first:
    http://localhost:9090/graph
Type the metric name and confirm it returns values before adding to Grafana.
The most common cause is metric name mismatch between dashboard queries
and what the exporter actually produces.

**Dashboard shows data but panels are empty after import:**
Community dashboards use metric names from specific exporter versions
that may not match yours. Build panels manually using the exact metric
names from your Prometheus autocomplete rather than importing dashboards.

## Teardown

    docker compose -f docker-compose-monitoring.yml down -v
