from .arxiv_parser import ARXIVParser
from .botocore_client import OBSWorker


data_range = ('2026-01-15', '2026-01-27')
obs = OBSWorker()
arx_parser = ARXIVParser(data_range)

async def sync_from_obs():
    await obs.download_all_md()
    await obs.download_metadata()

async def upload_data_obs():
    print("Подгрузка FAISS")
    await obs.upload_vectorstore()
    print("Подгрузка дайджестов")
    await  obs.upload_digests()
    await obs.upload_all_md()
    await obs.put_json()


async def run():
    try:
        print("Считываем файлы из OBS")
        await sync_from_obs()
        print("Файлы на локальной машине")
    except Exception as e:
        print("Статей в объектном хранилище нет!")
    # await arx_parser.run()
    # await upload_data_obs()

# if __name__ == "__main__":
#     asyncio.run(main())



