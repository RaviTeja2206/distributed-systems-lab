import json
import time
import logging
import signal
import sys
from kafka import KafkaConsumer
from kafka.errors import KafkaError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
logger = logging.getLogger("kafka-consumer")

# In both files, change BOOTSTRAP_SERVERS to:
BOOTSTRAP_SERVERS = ['localhost:29092', 'localhost:29093', 'localhost:29094']
TOPIC = 'media-ingest-jobs'
GROUP_ID = 'transcoding-workers-py'


def process_asset(message):
    """
    Simulates processing a media asset job.
    In production this would trigger a transcode, QC run, etc.
    """
    asset = message.value
    logger.info(
        f"Processing → "
        f"partition={message.partition} "
        f"offset={message.offset} "
        f"key={message.key} "
        f"asset={asset.get('file')} "
        f"client={asset.get('client')}"
    )
    # Simulate work
    time.sleep(0.5)
    logger.info(f"Completed → {asset.get('file')}")


def main():
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOTSTRAP_SERVERS,

        # Consumer group ID — all consumers with same group_id
        # share partition assignments. Each partition goes to
        # exactly one consumer in the group.
        group_id=GROUP_ID,

        # Deserialize JSON bytes back to Python dict
        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
        key_deserializer=lambda k: k.decode('utf-8') if k else None,

        # auto_offset_reset controls what happens when a consumer
        # group reads a topic for the first time:
        # 'earliest' = start from the beginning of the log
        # 'latest'   = only read messages published after this consumer started
        auto_offset_reset='earliest',

        # enable_auto_commit=True means Kafka automatically commits
        # offsets periodically. For production use False and commit
        # manually after processing — same concept as RabbitMQ acks.
        enable_auto_commit=True,
        auto_commit_interval_ms=1000,

        # How long to wait for new messages before returning empty poll
        consumer_timeout_ms=10000,
    )

    logger.info(f"Consumer started → group={GROUP_ID} topic={TOPIC}")
    logger.info("Waiting for messages... (Ctrl+C to stop)")

    # Handle graceful shutdown
    def shutdown(sig, frame):
        logger.info("Shutting down consumer...")
        consumer.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    try:
        for message in consumer:
            process_asset(message)
    except Exception as e:
        logger.error(f"Consumer error: {e}")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()