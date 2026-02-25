from contextlib import asynccontextmanager
from aiobotocore.session import AioSession
from botocore.client import Config
from dotenv import load_dotenv
from typing import List
import os
import json
import re
from pathlib import Path

load_dotenv()


class OBSWorker:
    def __init__(self):
        self.params = {
            "service_name": "s3",
            "aws_access_key_id": os.getenv("OBS_ACCESS_KEY"),
            "aws_secret_access_key": os.getenv("OBS_SECRET_KEY"),
            "region_name": os.getenv("OBS_REGION"),
            "endpoint_url": os.getenv("OBS_ENDPOINT"),
            "config": Config(s3={"addressing_style": "virtual"})
        }
        base_dir = Path(__file__).resolve().parent.parent  # agent_for_bz
        self.name_doc_dir = base_dir / os.getenv("STORAGE_PATH") / os.getenv("ARTICLES_FOLDER")
        self.name_meta_dir = base_dir / os.getenv("STORAGE_PATH")
        self.name_faiss_index = base_dir / os.getenv("FAISS_FOLDER")
        self.name_digests = base_dir / os.getenv("DIGESTS_FOLDER")
        self.bucket = os.getenv("OBS_BUCKET")

    @asynccontextmanager
    async def client(self):
        session = AioSession()
        async with session.create_client(**self.params) as client:
            yield client

    def extract_json_array(self, raw: str) -> str:
        start = raw.find("[")
        end = raw.rfind("]")

        if start == -1 or end == -1 or end < start:
            raise ValueError("JSON array not found in response")

        return raw[start:end + 1]

    async def download_metadata(self):

        local_dir = self.name_meta_dir
        os.makedirs(local_dir, exist_ok=True)
        async with self.client() as client:
            key = "metadata/metadata.json"
            local_path = local_dir / "metadata.json"
            try:
                response = await client.get_object(Bucket=self.bucket, Key=key)
            except:
                print("Файла метаданных нет!")
            data = await response["Body"].read()
            raw_text = data.decode("utf-8", errors="ignore")

            clean_json = self.extract_json_array(raw_text)
            data = json.loads(clean_json)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print("Downloaded metadata.json")


    def clean_text(self, text: str) -> str:
        lines = text.splitlines()

        while lines:
            line = lines[0].strip()

            if not line:
                lines.pop(0)
                continue

            if re.fullmatch(r'[0-9a-fA-F]{3,12}', line):
                lines.pop(0)
                continue

            if re.fullmatch(r'\d+', line):
                lines.pop(0)
                continue

            break

        pattern = r'\n(?:\d+\n)*x-amz-checksum-crc32:.*$'
        text = re.sub(pattern, '', text, flags=re.S)
        lines = text.splitlines()
        while lines and lines[-1].strip().isdigit() and len(lines[-1].strip()) < 5:
            lines.pop()

        text = '\n'.join(lines)

        return text.strip()

    async def download_all_md(self):
        local_dir = self.name_doc_dir
        os.makedirs(local_dir, exist_ok=True)
        async with self.client() as client:
            continuation_token = None

            while True:
                params = {
                    "Bucket": self.bucket,
                    "Prefix": "articles/"
                }

                if continuation_token:
                    params["ContinuationToken"] = continuation_token

                resp = await client.list_objects_v2(**params)

                if "Contents" not in resp:
                    break
                for obj in resp["Contents"]:
                    key = obj["Key"]

                    if not key.endswith(".md"):
                        continue

                    local_path = os.path.join(local_dir, os.path.basename(key))

                    response = await client.get_object(Bucket=self.bucket, Key=key)
                    data = await response["Body"].read()
                    text = data.decode("utf-8", errors="ignore")
                    cleaned = self.clean_text(text)

                    with open(local_path, "w", encoding="utf-8") as f:
                        f.write(cleaned)
                    # with open(local_path, "wb") as f:
                    #     f.write(data)

                    print(f"Downloaded {key}")

                if resp.get("IsTruncated"):
                    continuation_token = resp.get("NextContinuationToken")
                else:
                    break

    async def download_vectorstore(self):
        """Скачать файлы индекса (.faiss и .pkl) из OBS, проверяя изменения."""

        local_dir = self.name_faiss_index
        s3_prefix = "faiss_index/"
        os.makedirs(local_dir, exist_ok=True)

        async with self.client() as client:
            continuation_token = None
            files_downloaded = 0

            while True:
                params = {
                    "Bucket": self.bucket,
                    "Prefix": s3_prefix
                }
                if continuation_token:
                    params["ContinuationToken"] = continuation_token

                resp = await client.list_objects_v2(**params)

                if "Contents" not in resp:
                    break

                for obj in resp["Contents"]:
                    key = obj["Key"]

                    # Пропускаем "папки" и файлы не тех расширений
                    if key.endswith('/') or not (key.endswith('.faiss') or key.endswith('.pkl')):
                        continue

                    # Формируем локальный путь (сохраняем структуру, если есть подпапки, или просто имя файла)
                    relative_path = key.replace(s3_prefix, "", 1)
                    local_path = os.path.join(local_dir, relative_path)

                    # Создаем подпапки локально, если они есть в ключе S3
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)

                    remote_size = obj.get("Size", 0)
                    need_download = False

                    # Проверка: существует ли файл и совпадает ли размер
                    if not os.path.exists(local_path):
                        need_download = True
                        print(f"[{os.path.basename(key)}] Не найден локально.")
                    elif os.path.getsize(local_path) != remote_size:
                        need_download = True
                        print(
                            f"[{os.path.basename(key)}] Размер изменен (S3: {remote_size}, Local: {os.path.getsize(local_path)}). Обновляем.")
                    else:
                        # Файл актуален
                        continue

                    if need_download:
                        try:
                            # Скачиваем бинарный поток
                            response = await client.get_object(Bucket=self.bucket, Key=key)
                            data = await response["Body"].read()

                            # Пишем как бинарный файл (важно для .faiss и .pkl!)
                            with open(local_path, "wb") as f:
                                f.write(data)

                            print(f"Загружен: {key}")
                            files_downloaded += 1

                        except Exception as e:
                            print(f"Ошибка при скачивании {key}: {e}")

                if resp.get("IsTruncated"):
                    continuation_token = resp.get("NextContinuationToken")
                else:
                    break

            if files_downloaded == 0:
                print("Все файлы индексов актуальны.")
            else:
                print(f"Синхронизация завершена. Скачано файлов: {files_downloaded}")

    async def put_json(self):
        local_path = self.name_meta_dir / "metadata.json"
        with open(local_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        json_bytes = json.dumps(
            data, ensure_ascii=False, indent=2
        ).encode("utf-8")

        async with self.client() as client:
            await client.put_object(
                Bucket=self.bucket,
                Key="metadata/metadata.json",
                Body=json_bytes,
                ContentType="application/json"
            )

    async def upload_all_md(self):
        async with self.client() as client:
            for filename in os.listdir(self.name_doc_dir):
                if not filename.endswith(".md"):
                    continue

                local_path = self.name_doc_dir / filename
                key = f"articles/{filename}"

                with open(local_path, "rb") as f:
                    data = f.read()

                await client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType="text/markdown",
                    ContentLength=len(data)
                )


    async def delete_dir(self, prefix: str):
        """
        Удаляет все объекты по заданному префиксу (папку) в OBS/S3.
        """

        continuation_token = None
        async with self.client() as client:
            while True:
                list_params = {
                    "Bucket": self.bucket,
                    "Prefix": prefix
                }

                if continuation_token:
                    list_params["ContinuationToken"] = continuation_token

                resp = await client.list_objects_v2(**list_params)

                # Нет объектов
                if "Contents" not in resp:
                    break

                # Удаляем пачками
                delete_payload = {
                    "Objects": [{"Key": obj["Key"]} for obj in resp["Contents"]],
                    "Quiet": True
                }

                await client.delete_objects(
                    Bucket=self.bucket,
                    Delete=delete_payload
                )

                # Проверяем, есть ли ещё страницы
                if resp.get("IsTruncated"):
                    continuation_token = resp.get("NextContinuationToken")
                else:
                    break

            print(f"Удалено всё по пути: {prefix}")

    async def upload_vectorstore(self):
        """Загрузить всю папку faiss_index в OBS"""
        faiss_s3_prefix = "faiss_index/"
        local_faiss_dir = self.name_faiss_index

        async with self.client() as client:
            # 1. Собрать S3 файлы с метаданными
            s3_files = {}
            continuation_token = None
            while True:
                params = {
                    "Bucket": self.bucket,
                    "Prefix": faiss_s3_prefix
                }
                if continuation_token:
                    params["ContinuationToken"] = continuation_token

                resp = await client.list_objects_v2(**params)
                if "Contents" not in resp:
                    break

                for obj in resp["Contents"]:
                    s3_key = obj["Key"]
                    s3_files[s3_key] = {
                        "size": obj.get("Size"),
                        "etag": obj.get("ETag", "").strip('"')
                    }

                if resp.get("IsTruncated"):
                    continuation_token = resp.get("NextContinuationToken")
                else:
                    break

            # 2. Перезаписать изменённые/новые
            for root, _, files in os.walk(local_faiss_dir):
                for file in files:
                    local_path = os.path.join(root, file)
                    s3_key = faiss_s3_prefix + os.path.relpath(local_path, local_faiss_dir)

                    local_size = os.path.getsize(local_path)

                    if s3_key not in s3_files:
                        # Новый файл
                        await self._upload_file(client, local_path, s3_key)
                        print(f"Новый: {file}")
                    elif s3_files[s3_key]["size"] != local_size:
                        # Изменился размер
                        await self._upload_file(client, local_path, s3_key)
                        print(f"Обновлён: {file}")
                    else:
                        print(f"Актуален: {file}")

            print("FAISS синхронизирован с перезаписью изменений")

    async def _upload_file(self, client, local_path, s3_key):
        """Вспомогательная загрузка одного файла"""
        with open(local_path, "rb") as f:
            await client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=f
            )
        print(f"Загружен {s3_key}")

    async def ensure_article_downloaded(self, filename: str):
        """Скачать конкретный .md файл если нет локально"""
        articles_path = self.name_doc_dir
        local_path = os.path.join(articles_path, filename)

        if os.path.exists(local_path):
            print(f"{filename} уже есть локально")
            return

        print(f"Скачиваем {filename}...")
        s3_key = f"articles/{filename}"
        os.makedirs(articles_path, exist_ok=True)

        async with self.client() as client:
            try:
                response = await client.get_object(Bucket=self.bucket, Key=s3_key)
                data = await response["Body"].read()
                with open(local_path, "wb") as f:
                    f.write(data)
                print(f"{filename} скачан")
            except Exception as e:
                print(f"{filename} не найден в S3: {e}")

    async def upload_single_digest_cond(self, digest_path: str, filename):
        """Загрузить один дайджест filename.json в S3"""

        if not os.path.exists(digest_path):
            print(f"Дайджест {digest_path} не найден")
            return

        s3_key = f"digests/{filename}"
        async with self.client() as client:
            await self._upload_file(client, digest_path, s3_key)
        print(f"Дайджест {filename} S3")

    async def upload_single_digest(self, digest_path: str, filename):
        """Загрузить один дайджест filename.json в S3"""

        if not os.path.exists(digest_path):
            print(f"Дайджест {digest_path} не найден")
            return

        s3_key = f"reviews/{filename}" # Тут был конец json, может такая задумка?)
        async with self.client() as client:
            await self._upload_file(client, digest_path, s3_key)
        print(f"Дайджест {filename}.json S3")

    async def download_reviews(self):
        local_dir = Path(self.name_digests)
        os.makedirs(local_dir, exist_ok=True)

        prefix = "reviews/"

        async with self.client() as client:
            paginator = client.get_paginator('list_objects_v2')

            # 1. Получаем список всех объектов с префиксом "reviews/"
            all_keys: List[str] = []

            try:
                async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            key = obj['Key']
                            # Игнорируем сами "папки" (ключи, заканчивающиеся на /), если они есть
                            if not key.endswith('/'):
                                all_keys.append(key)
            except Exception as e:
                print(f"Ошибка при списке объектов: {e}")
                return

            if not all_keys:
                print(f"Объектов с префиксом '{prefix}' не найдено.")
                return

            print(f"Найдено файлов для скачивания: {len(all_keys)}")

            # 2. Скачиваем каждый файл
            for s3_key in all_keys:
                # Формируем локальный путь
                # Убираем префикс "reviews/" из начала ключа, чтобы получить относительный путь
                relative_path = s3_key[len(prefix):]
                local_file_path = local_dir / relative_path

                # Создаем локальные подпапки, если их нет
                local_file_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    response = await client.get_object(Bucket=self.bucket, Key=s3_key)

                    # Читаем поток данных
                    async with response['Body'] as stream:
                        data = await stream.read()
                        text = data.decode("utf-8", errors="ignore")
                        cleaned = self.clean_text(text)

                    # Сохраняем на диск (режим 'wb' для бинарных данных, работает и для текста)
                    with open(local_file_path, 'w', encoding="utf-8") as f:
                        f.write(cleaned)

                    print(f"Скачан: {s3_key} -> {local_file_path}")

                except Exception as e:
                    print(f"Ошибка при скачивании {s3_key}: {e}")

        print("Загрузка завершена.")

    async def upload_digests(self):
        """Загрузить ВСЕ новые .md дайджесты из локальной папки digest/ и её подпапок"""
        digest_root = self.name_digests

        if not digest_root.exists():
            print("Папка digests/ не существует")
            return

        uploaded = 0
        async with self.client() as client:
            # Рекурсивно обходим все .md файлы
            for md_file in digest_root.rglob("*.md"):
                print(md_file)
                # Относительный путь от digest_root (например: Общие_принципы/file.md)
                rel_path = md_file.relative_to(digest_root)
                print(rel_path)
                # Ключ в S3: digest/Общие_принципы/file.md
                s3_key = f"reviews/{rel_path.as_posix()}"

                try:
                    # Проверяем, существует ли уже в S3
                    await client.head_object(Bucket=self.bucket, Key=s3_key)
                    print(f"{s3_key} уже в S3")
                except Exception:
                    # Любой exception (404 или другая ошибка) → считаем, что нужно загрузить
                    await self._upload_file(client, str(md_file), s3_key)
                    uploaded += 1

        print(f"Загружено дайджестов: {uploaded}")

    async def run_download(self):
        await self.download_metadata()
        await self.download_all_md()
        # await self.download_reviews()

    # async def run_download(self):
    #     bucket = self.bucket
    #     await self.download_metadata(bucket)
    #     await self.download_all_md(bucket)
    #
    # async def run_upload(self):
    #     bucket = self.bucket
    #     await self.put_json(bucket)
    #     await self.upload_all_md(bucket)

async def main():
    obs = OBSWorker()
    # await obs.run_download()
    await obs.download_vectorstore()


    # parser.run() / agent / обработка

    # await obs.delete_dir(bucket, "articles/")
    # await obs.delete_dir(bucket, "metadata/")

    # await obs.put_json(bucket)
  #  await obs.upload_all_md(bucket)

import asyncio

if __name__ == "__main__":
    asyncio.run(main())