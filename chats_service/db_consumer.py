# To consume latest messages and auto-commit offsets
import asyncio
from kafka import KafkaConsumer
import json
import logging
from db_scripts.chats_tables import DataBaseChats

logging.basicConfig(level=logging.INFO)

async def consume_messages():
    db = DataBaseChats()
    await db.init_db_pool()

    consumer = KafkaConsumer(
        'chat-messages',
        bootstrap_servers=['localhost:9092'],
        group_id='user-group',
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest'
    )

    logging.info("Консьюмер слушает")

    for message in consumer:
        payload = message.value
        user_id = payload.get("user_id")
        chat_id = payload.get("chat_id")
        text = payload.get("message")
        is_from_bot = payload.get("is_from_bot")

        if user_id and text:
            await db.add_message(user_id, text, chat_id, is_from_bot)
            logging.info(f"Сообщение from user={user_id} chat={chat_id}")
        else:
            logging.warning(f"Invalid: {payload}")



if __name__ == "__main__":
    asyncio.run(consume_messages())

#                  TEST                      #
# consumer = KafkaConsumer(
#     'json-topic-2',
#     bootstrap_servers=['localhost:9092'],
#     group_id='user-group',
#     value_deserializer=lambda m: json.loads(m.decode('utf-8')),
#     auto_offset_reset='earliest'
# )
#
# print("📡 Listening to Kafka topic...")
#
# for message in consumer:
#     print(f"Topic: {message.topic} | Partition: {message.partition} | Offset: {message.offset}")
#     print("Payload:", message.value)

