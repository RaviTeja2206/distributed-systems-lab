import json
import time
import logging
from kafka import KafkaProducer
from kafka.errors import KafkaError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)
logger = logging.getLogger("kafka-producer")

# Bootstrap servers — give Kafka all 3 brokers
# Kafka client will discover the full cluster from any of them
# This is different from RabbitMQ where you connect to one broker
# In both files, change BOOTSTRAP_SERVERS to:
BOOTSTRAP_SERVERS = ['localhost:29092', 'localhost:29093', 'localhost:29094']
TOPIC = 'media-ingest-jobs'


def on_send_success(record_metadata):
    """Called when a message is successfully acknowledged by the broker."""
    logger.info(
        f"Message delivered → "
        f"topic={record_metadata.topic} "
        f"partition={record_metadata.partition} "
        f"offset={record_metadata.offset}"
    )


def on_send_error(exception):
    """Called when a message fails to deliver."""
    logger.error(f"Message delivery failed: {exception}")


def main():
    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP_SERVERS,

        # Serialize values to JSON bytes
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),

        # Serialize keys to bytes — key determines partition
        key_serializer=lambda k: k.encode('utf-8'),

        # acks='all' means wait for all in-sync replicas to acknowledge
        # This is the maximum durability setting
        # Compare to acks=1 (leader only) or acks=0 (fire and forget)
        acks='all',

        # Retry up to 3 times on transient failures
        retries=3,

        # Idempotent producer — prevents duplicate messages on retry
        # Requires acks='all'
        enable_idempotence=True,
    )

    logger.info(f"Producer connected to: {BOOTSTRAP_SERVERS}")
    logger.info(f"Publishing to topic: {TOPIC}")

    assets = [
        {"asset_id": "asset001", "type": "video", "file": "cbs_news_hd.mxf",      "client": "paramount"},
        {"asset_id": "asset002", "type": "audio", "file": "fox_promo.wav",          "client": "fox"},
        {"asset_id": "asset003", "type": "video", "file": "pbs_doc_4k.mov",         "client": "pbs"},
        {"asset_id": "asset004", "type": "video", "file": "paramount_hdr.mxf",      "client": "paramount"},
        {"asset_id": "asset005", "type": "audio", "file": "cbs_sports.wav",         "client": "paramount"},
        {"asset_id": "asset006", "type": "video", "file": "fox_news_live.mxf",      "client": "fox"},
        {"asset_id": "asset007", "type": "video", "file": "pbs_station_id.mov",     "client": "pbs"},
        {"asset_id": "asset008", "type": "video", "file": "cbs_drama_ep01.mxf",     "client": "paramount"},
        {"asset_id": "asset009", "type": "audio", "file": "fox_sports_audio.wav",   "client": "fox"},
    ]

    for asset in assets:
        # Key is the asset_id — guarantees all events for the same
        # asset always go to the same partition, preserving order
        key = asset["asset_id"]

        future = producer.send(
            TOPIC,
            key=key,
            value=asset
        )

        # Register callbacks — non-blocking
        future.add_callback(on_send_success)
        future.add_errback(on_send_error)

        logger.info(f"Queued: {key} → {asset['file']}")
        time.sleep(0.1)

    # Flush ensures all queued messages are sent before exiting
    # Without this, buffered messages may be lost on process exit
    logger.info("Flushing producer buffer...")
    producer.flush()
    producer.close()
    logger.info("Producer done.")


if __name__ == "__main__":
    main()