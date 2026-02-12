from contextlib import asynccontextmanager
from aiobotocore.session import AioSession
from botocore.client import Config
from dotenv import load_dotenv
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
        base_dir = Path(__file__).resolve().parent  # agent_for_bz
        self.name_doc_dir = base_dir / os.getenv("STORAGE_PATH") / os.getenv("ARTICLES_FOLDER")
        self.name_meta_dir = base_dir / os.getenv("STORAGE_PATH")
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
            except client.exceptions.NoSuchKey:
                print("Файла метаданных нет в OBS — создаём новый при необходимости.")
                with open(local_path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
                return
            except Exception as e:
                print(f"Ошибка при загрузке metadata.json: {e}")
                with open(local_path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
                return

            data = await response["Body"].read()
            raw_text = data.decode("utf-8", errors="ignore")
            clean_json = self.extract_json_array(raw_text)
            parsed_data = json.loads(clean_json)

            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(parsed_data, f, ensure_ascii=False, indent=2)

            print("Downloaded metadata.json")


    def clean_text(self, text: str) -> str:
        lines = text.splitlines()

        while lines:
            line = lines[0].strip()

            if not line:
                lines.pop(0)
                continue

            if re.fullmatch(r'[0-9a-fA-F]{3,8}', line):
                lines.pop(0)
                continue

            if len(line) < 10 and ' ' not in line:
                lines.pop(0)
                continue

            break

        text = '\n'.join(lines)
        text = re.sub(
            r'\n\d+\n0\nx-amz-checksum-crc32:.*$',
            '',
            text,
            flags=re.S
        )
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

        s3_key = f"reviews/{filename}.json"
        async with self.client() as client:
            await self._upload_file(client, digest_path, s3_key)
        print(f"Дайджест {filename}.json S3")


    async def run_download(self):
        bucket = self.bucket
        await self.download_metadata()
        await self.download_all_md()

    # async def run_download(self):
    #     bucket = self.bucket
    #     await self.download_metadata(bucket)
    #     await self.download_all_md(bucket)
    #
    # async def run_upload(self):
    #     bucket = self.bucket
    #     await self.put_json(bucket)
    #     await self.upload_all_md(bucket)

# async def main():
#     obs = OBSWorker()
#     # await obs.run_download()
#     await obs.run_upload()
#
#
#     # parser.run() / agent / обработка
#
#     # await obs.delete_dir(bucket, "articles/")
#     # await obs.delete_dir(bucket, "metadata/")
#
#     # await obs.put_json(bucket)
#   #  await obs.upload_all_md(bucket)
#
#
# if __name__ == "__main__":
#     asyncio.run(main())