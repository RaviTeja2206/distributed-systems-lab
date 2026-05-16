# JMX Prometheus Java Agent

The jmx_prometheus_javaagent.jar file is not committed to this repository
because it is a binary file.

Download it before starting the monitoring stack:

    curl -Lo jmx/jmx_prometheus_javaagent.jar \
      https://repo1.maven.org/maven2/io/prometheus/jmx/jmx_prometheus_javaagent/0.19.0/jmx_prometheus_javaagent-0.19.0.jar

Verify it downloaded correctly:

    ls -lh jmx/jmx_prometheus_javaagent.jar
