import pika
import json
import time
import logging
import sys
from consul_client import ConsulClient

# Configure logging — in production this goes to a log aggregator
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("worker")

# Configuration
CONSUL_HOST = "localhost"
CONSUL_PORT = 8500
RABBITMQ_SERVICE_NAME = "rabbitmq"
QUEUE_NAME = "lab-jobs"
WORKER_ID = "worker-1"
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 3  # seconds between reconnect attempts


def process_job(channel, method, properties, body):
    """
    Called by pika for every message delivered from the queue.
    
    The three-parameter signature (channel, method, properties, body)
    is required by pika's consumer callback interface.
    """
    try:
        job = json.loads(body)
        job_id = job.get("job_id", "unknown")
        
        logger.info(f"[{WORKER_ID}] Processing job {job_id}: {job}")
        
        # Simulate actual work
        time.sleep(1)
        
        # Acknowledge — tells RabbitMQ this message was processed successfully
        # Without this ack, RabbitMQ re-queues the message when the consumer dies
        channel.basic_ack(delivery_tag=method.delivery_tag)
        
        logger.info(f"[{WORKER_ID}] Completed job {job_id}")

    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in message: {body}")
        # Negative ack — tell RabbitMQ to discard this malformed message
        # requeue=False means don't put it back on the queue
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    except Exception as e:
        logger.error(f"Job processing failed: {e}")
        # Negative ack with requeue=True — put it back for another worker to try
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def connect_to_rabbitmq(consul):
    """
    Uses Consul to discover a RabbitMQ node and connect to it.
    
    This is the core service discovery pattern:
    1. Ask Consul for a healthy RabbitMQ instance
    2. Connect to whatever Consul returns
    3. No hardcoded IPs anywhere
    
    If Consul returns no healthy instances, we wait and retry.
    """
    for attempt in range(MAX_RECONNECT_ATTEMPTS):
        logger.info(f"Querying Consul for RabbitMQ... (attempt {attempt + 1})")
        
        instance = consul.get_random_healthy_service(RABBITMQ_SERVICE_NAME)
        
        if not instance:
            logger.warning("No healthy RabbitMQ instances in Consul")
            logger.info(f"Retrying in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)
            continue

        host, port = instance
        logger.info(f"Consul returned RabbitMQ at {host}:{port}")

        try:
            credentials = pika.PlainCredentials("admin", "password")
            parameters = pika.ConnectionParameters(
                host=host,
                port=port,
                credentials=credentials,
                # heartbeat keeps connection alive and detects dead connections
                heartbeat=60,
                # how long to wait for connection before giving up
                blocked_connection_timeout=30,
                connection_attempts=2,
                retry_delay=2
            )
            connection = pika.BlockingConnection(parameters)
            logger.info(f"Connected to RabbitMQ at {host}:{port}")
            return connection

        except pika.exceptions.AMQPConnectionError as e:
            logger.error(f"Failed to connect to {host}:{port} — {e}")
            logger.info(f"Will ask Consul for another node in {RECONNECT_DELAY}s")
            time.sleep(RECONNECT_DELAY)

    logger.error("Exhausted all reconnect attempts")
    return None


def run_worker(consul):
    """
    Main worker loop with automatic reconnection.
    
    When the RabbitMQ connection drops (node failure, network issue),
    the worker catches the exception, re-queries Consul for a healthy
    node, and reconnects. From the application's perspective the worker
    never goes down — it just briefly pauses while reconnecting.
    """
    while True:
        connection = connect_to_rabbitmq(consul)
        
        if not connection:
            logger.error("Could not connect to RabbitMQ. Exiting.")
            sys.exit(1)

        try:
            channel = connection.channel()

            # Declare queue — idempotent, safe to call even if queue exists
            channel.queue_declare(
                queue=QUEUE_NAME,
                durable=True  # queue survives RabbitMQ restart
            )

            # prefetch_count=1 = fair dispatch
            # worker only gets a new message after acknowledging the current one
            channel.basic_qos(prefetch_count=1)

            channel.basic_consume(
                queue=QUEUE_NAME,
                on_message_callback=process_job
            )

            logger.info(f"[{WORKER_ID}] Waiting for jobs on queue '{QUEUE_NAME}'")
            logger.info("Press Ctrl+C to stop")

            # This blocks and processes messages until connection drops
            channel.start_consuming()

        except pika.exceptions.ConnectionClosedByBroker:
            logger.warning("Connection closed by broker — reconnecting via Consul")
            time.sleep(RECONNECT_DELAY)

        except pika.exceptions.AMQPChannelError as e:
            logger.error(f"Channel error: {e} — reconnecting")
            time.sleep(RECONNECT_DELAY)

        except pika.exceptions.AMQPConnectionError:
            logger.warning("Connection lost — re-querying Consul for healthy node")
            time.sleep(RECONNECT_DELAY)

        except KeyboardInterrupt:
            logger.info("Shutting down worker")
            if connection and not connection.is_closed:
                connection.close()
            break


def main():
    consul = ConsulClient(consul_host=CONSUL_HOST, consul_port=CONSUL_PORT)
    
    # On startup — show what Consul knows about RabbitMQ
    logger.info("=== Service Discovery on Startup ===")
    all_instances = consul.get_service_catalog(RABBITMQ_SERVICE_NAME)
    logger.info(f"All registered RabbitMQ instances: {len(all_instances)}")
    for inst in all_instances:
        addr = inst["ServiceAddress"]
        port = inst["ServicePort"]
        logger.info(f"  {addr}:{port}")

    healthy = consul.get_healthy_service(RABBITMQ_SERVICE_NAME)
    logger.info(f"Healthy RabbitMQ instances: {len(healthy)}")
    for addr, port in healthy:
        logger.info(f"  {addr}:{port} ✓")
    logger.info("====================================")

    run_worker(consul)


if __name__ == "__main__":
    main()