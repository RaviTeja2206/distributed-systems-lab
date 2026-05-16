import pika
import json
import time
import logging
import sys
from consul_client import ConsulClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)
logger = logging.getLogger("producer")

CONSUL_HOST = "localhost"
CONSUL_PORT = 8500
RABBITMQ_SERVICE_NAME = "rabbitmq"
QUEUE_NAME = "lab-jobs"


def get_rabbitmq_connection(consul):
    instance = consul.get_random_healthy_service(RABBITMQ_SERVICE_NAME)
    
    if not instance:
        logger.error("No healthy RabbitMQ instances found in Consul")
        sys.exit(1)
    
    host, port = instance
    logger.info(f"Consul returned RabbitMQ at {host}:{port}")
    
    credentials = pika.PlainCredentials("admin", "password")
    parameters = pika.ConnectionParameters(
        host=host,
        port=port,
        credentials=credentials
    )
    return pika.BlockingConnection(parameters)


def main():
    consul = ConsulClient(consul_host=CONSUL_HOST, consul_port=CONSUL_PORT)
    connection = get_rabbitmq_connection(consul)
    channel = connection.channel()
    
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    
    for i in range(20):
        job = {
            "job_id": i,
            "task": f"process_asset_{i:03d}",
            "asset_type": "video" if i % 2 == 0 else "audio",
            "priority": "high" if i < 5 else "normal"
        }
        
        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(job),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        logger.info(f"Published job {i}: {job['task']}")
        time.sleep(0.2)
    
    logger.info("All jobs published")
    connection.close()


if __name__ == "__main__":
    main()