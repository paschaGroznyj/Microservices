from kafka import KafkaProducer
from kafka.errors import KafkaError
from utils.config import KAFKA_BROKER
import msgpack
import json
import os
# producer = KafkaProducer(bootstrap_servers=['localhost:9092'])
#
# # Asynchronous by default
# future = producer.send('my-topic', b'raw_bytes')
#
# # Block for 'synchronous' sends
# try:
#     record_metadata = future.get(timeout=10)
#     # Successful result returns assigned partition and offset
#     print(record_metadata.topic)
#     print(record_metadata.partition)
#     print(record_metadata.offset)
#
# except KafkaError:
#     # Decide what to do if produce request failed...
#     log.exception()
#     pass
#
#
# # produce keyed messages to enable hashed partitioning
# producer.send('my-topic', key=b'foo', value=b'bar')
#
# # encode objects via msgpack
# producer = KafkaProducer(value_serializer=msgpack.dumps)
# producer.send('msgpack-topic', {'key': 'value'})

# produce json messages
# data = {
#     'message': 'хелло',
#     'time': '2025-12-12-05:05:12'
# }

# KAFKA_BROKER = os.getenv("KAFKA_BROKER")
producer = KafkaProducer(
    bootstrap_servers=[KAFKA_BROKER],
    api_version=(4,1,0),
    key_serializer=lambda k: str(k).encode('utf-8'),
    value_serializer=lambda m: json.dumps(m).encode('utf-8'))
# producer.send('json-topic-2', key= 'user2', value=data)
# producer.flush()


# # produce asynchronously
# for _ in range(100):
#     producer.send('my-topic', b'msg')
#
# def on_send_success(record_metadata):
#     print(record_metadata.topic)
#     print(record_metadata.partition)
#     print(record_metadata.offset)
#
# def on_send_error(excp):
#     log.error('I am an errback', exc_info=excp)
#     # handle exception
#
# # produce asynchronously with callbacks
# producer.send('my-topic', b'raw_bytes').add_callback(on_send_success).add_errback(on_send_error)
#
# # block until all async messages are sent
#
# # configure multiple retries
# producer = KafkaProducer(retries=5)

