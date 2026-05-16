# consumer.py — reads messages from the queue
import pika
import json
import time

def process_job(ch, method, properties, body):
    job = json.loads(body)
    print(f"Processing job: {job['job_id']}")
    time.sleep(1)  # simulate work
    ch.basic_ack(delivery_tag=method.delivery_tag)  # tell RabbitMQ: done
    print(f"Finished job: {job['job_id']}")

connection = pika.BlockingConnection(
    pika.ConnectionParameters(host='localhost', port=5672,
        credentials=pika.PlainCredentials('admin', 'password'))
)
channel = connection.channel()
channel.queue_declare(queue='lab-jobs', durable=True)
channel.basic_qos(prefetch_count=1)  # take one job at a time
channel.basic_consume(queue='lab-jobs', on_message_callback=process_job)

print("Waiting for jobs...")
channel.start_consuming()