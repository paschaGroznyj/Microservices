from kafka import KafkaConsumer
import json

class UniverseConsumer:

    def chat_consumer(self):
        return KafkaConsumer('chat-messages',
                                 group_id='my-group',
                                 bootstrap_servers=['localhost:9092'],
                                 value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                                 auto_offset_reset='earliest')
