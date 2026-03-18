# import os
# import json
# import re
# import time
# import asyncio
# import random
# from datetime import datetime
# from telethon import TelegramClient
# from telethon.tl.functions.messages import GetDialogsRequest, GetHistoryRequest
# from telethon.tl.types import InputPeerEmpty, PeerChannel, MessageService
# from dotenv import load_dotenv
# from pathlib import Path
# from core.config import TG_API_ID, TG_API_HASH, TELEPHONE_NUMBER, NEED_CHATS, TG_BOT_TOKEN
# # from db.app_db import DataBaseMessages
# from datetime import datetime
#
# from telethon.errors import FloodWaitError
#
# async def safe_get_entity(client, peer):
#     try:
#         return await client.get_entity(peer)
#     except FloodWaitError as e:
#         print(f"⚠️ FloodWait: ждём {e.seconds} сек")
#         await asyncio.sleep(e.seconds + 5)  # +5 сек запаса
#         return await client.get_entity(peer)  # повторная попытка
#     except Exception as e:
#         print(f"❌ Ошибка получения entity: {e}")
#         return None
#
# class ParserTG:
#     def __init__(self):
#         self.URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
#         api_id = int(TG_API_ID)
#         api_hash = TG_API_HASH
#         phone = TELEPHONE_NUMBER
#         self.dict_chats_title = NEED_CHATS
#         self.data_path = Path(__file__).resolve().parent
#         print(self.data_path)
#         self.client = TelegramClient(phone, api_id, api_hash)
#         # self.db = DataBaseMessages()
#
#
#     async def init_db(self):
#         await self.db.init_db_pool()
#
#     async def start_client(self):
#         await self.client.start()
#
#     def get_internal_chat_id_by_title(self, title):
#         for key, value in self.dict_chats_title.items():
#             if value == title:
#                 return key
#     async def get_all_dialogs(self, channel_id: int):
#         print("Загружаем список чатов...")
#         dialogs = await self.client.get_dialogs(limit=None)
#
#         for dialog in dialogs:
#             chat = dialog.entity
#             id_channel = getattr(chat, 'id')
#             if id_channel == channel_id:
#                 return chat
#         return ValueError(f"Группа с id'{channel_id}' не найдена!")
#
#     def __remove_emojis(self, text: str) -> str:
#         emoji_pattern = re.compile(
#             "["
#             "\U0001F600-\U0001F64F"
#             "\U0001F300-\U0001F5FF"
#             "\U0001F680-\U0001F6FF"
#             "\U0001F1E0-\U0001F1FF"
#             "\U00002702-\U000027B0"
#             "\U000024C2-\U0001F251"
#             "\U0001FA00-\U0001FA9F"
#             "]+",
#             flags=re.UNICODE,
#         )
#         return emoji_pattern.sub(r'', text)
#
#
#
#     async def parsing_dialogs(self, chat, chat_id, total_limit=500, offset_id=0, limit=100):
#         all_messages = []
#         total_count = 0
#
#         while True:
#             history = await self.client(GetHistoryRequest(
#                 peer=chat,
#                 offset_id=offset_id,
#                 offset_date=None,
#                 add_offset=0,
#                 limit=limit,
#                 max_id=0,
#                 min_id=0,
#                 hash=0
#             ))
#
#             if not history.messages:
#                 break
#
#             messages = history.messages
#             for msg in messages:
#                 # Пропускаем служебные сообщения (вступления, закрепы и т.д.)
#                 if isinstance(msg, MessageService):
#                     continue
#
#                 if len(msg.message) < 256: # Средняя длина сообщений 743 и 1343 для Гении и IAD соответственно
#                     continue
#                 links = self.URL_PATTERN.findall(msg.message)
#                 # Основные данные
#                 msg_data = {
#                     # "id": total_count + 1,
#                     "chat_id": chat_id,
#                     "message_id": msg.id,
#                     "date": datetime.strftime(msg.date, '%Y-%m-%d %H:%M:%S') if msg.date else None,
#                     "text": msg.message,
#                     "is_reply": msg.reply_to is not None,
#                     "reply_to_msg_id": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
#                     "sender": None,
#                     "forwarded_from": None,
#                     "source_links": links
#                 }
#                 # === Отправитель ===
#                 sender = None
#                 if msg.from_id:
#                     try:
#                         sender = await self.client.get_entity(msg.from_id)
#                     except Exception:
#                         pass  # если пользователь удалён или недоступен
#
#                 if sender:
#                     msg_data["sender"] = {
#                         "id": sender.id,
#                         "username": getattr(sender, 'username', None),
#                         "first_name": getattr(sender, 'first_name', None),
#                         "last_name": getattr(sender, 'last_name', None),
#                     }
#
#                 # === Пересылка ===
#                 if msg.fwd_from:
#                     fwd = msg.fwd_from
#                     fwd_info = {}
#                     # try:
#                     #     print(msg)
#                     # except Exception as e:
#                     #     print("next")
#                     if fwd.from_name:
#                         fwd_info["sender_name"] = fwd.from_name
#
#                     await asyncio.sleep(random.uniform(0.7, 1.8))
#                     if fwd.channel_post:
#                         # Пересылка из канала
#                         try:
#                             origin_chat = await self.client.get_entity(fwd.from_id)
#                             # print(origin_chat)
#                             fwd_info["chat_title"] = getattr(origin_chat, 'title', None)
#                         except:
#                             pass
#                     if fwd.from_id and fwd.channel_post:
#                         channel_id = fwd.from_id.channel_id
#                         channel_post = fwd.channel_post
#                         url = f"https://t.me/c/{channel_id}/{channel_post}"
#                         fwd_info["from_id"] = channel_id
#                         fwd_info["channel_post"] = channel_post
#                         fwd_info["from_url"] = url
#                     if fwd.date:
#                         fwd_info["date"] = datetime.strftime(fwd.date, '%Y-%m-%d %H:%M:%S') if fwd.date else None
#                     msg_data["forwarded_from"] = fwd_info
#
#                 all_messages.append(msg_data)
#                 total_count += 1
#                 await asyncio.sleep(random.uniform(0.7, 1.8))
#                 if total_limit and total_count >= total_limit:
#                     break
#
#             if total_limit and total_count >= total_limit:
#                 break
#
#             offset_id = messages[-1].id
#             print(f"Загружено {len(all_messages)} сообщений...")
#
#         # print("Сохранение сообщений в БД")
#         # await self.db.add_messages(messages=all_messages)
#         print("Cообщения сохранены")
#         # # === Сохранение в JSON ===
#         json_dir = self.data_path / "bag_messages"
#         os.makedirs(json_dir, exist_ok=True)
#         output_file = f"{json_dir}/IAD.json"
#         with open(output_file, "w", encoding="utf-8") as f:
#             json.dump(all_messages, f, ensure_ascii=False, indent=2)
#
#
#
# # update_json(Path("chat_[IAD]_ЦК_ГенИИ.json"))