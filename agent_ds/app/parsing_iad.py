import json
import os
import glob
import re
from datetime import datetime


path = r"C:\Users\Pavel\Desktop\Work\Внешняя_БЗ"

files = glob.glob(os.path.join(path, "*/*.json"))
print(files)
for file in files[:1]:
    with open(file, mode='r', encoding='utf-8') as f:
        data = json.load(f)


def parse_telegram_message(msg, channel: str = None):
    full_text = []
    links = []
    hashtags = []
    text = msg.get("text", [])
    for part in text:
        if isinstance(part, str):
            full_text.append(part)
        elif isinstance(part, dict):
            t = part.get("type")
            txt = part.get("text", "")

            full_text.append(txt)

            if t == "text_link":
                links.append(part.get("href"))

            elif t == "hashtag":
                hashtags.append(txt)

    text = "".join(full_text).strip()
    all_links = re.findall(r'https?://[^\s]+', text)

    if len(text) < 256 and len(all_links) == 0:
        return None

    links.extend(all_links)
    return {
        "id": msg.get("id"),
        "channel": channel,
        "date": datetime.strptime(msg.get("date"), "%Y-%m-%dT%H:%M:%S"),
        "author": msg.get("from"),
        "source": msg.get("forwarded_from"),
        "text": "".join(full_text).strip(),
        "links": links,
        "hashtags": hashtags
    }

insert_raw_data = []
for msg in data['messages']:
    filter_msg = parse_telegram_message(msg, channel = data['name'])
    if filter_msg:
        insert_raw_data.append(filter_msg)

from db.app_db import DataBaseMessages
import asyncio

db = DataBaseMessages()

async def main():
    await db.init_db_pool()
    await db.insert_raw_data(insert_raw_data)

if __name__ == '__main__':
    asyncio.run(main())