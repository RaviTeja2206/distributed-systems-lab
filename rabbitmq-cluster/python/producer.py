# install the library first
# pip install pika

# producer.py — publishes messages to a queue
import pika
import json
import time

connection = pika.BlockingConnection(
    pika.ConnectionParameters(host='localhost', port=5672,
        credentials=pika.PlainCredentials('admin', 'password'))
)
channel = connection.channel()

# Declare the queue — idempotent, safe to run multiple times
channel.queue_declare(queue='lab-jobs', durable=True)

for i in range(1,21):
    message = json.dumps({'job_id': i, 'task': f'process_item_{i}'})
    channel.basic_publish(
        exchange='',
        routing_key='lab-jobs',
        body=message,
        properties=pika.BasicProperties(delivery_mode=2)  # persistent
    )
    print(f"Published: {message}")
    time.sleep(0.5)

connection.close()