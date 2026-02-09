from .arxiv_parser import ARXIVParser
from .botocore_client import OBSWorker
from .fluxicon_parser import FluxiconParser


data_range = ('2026-01-15', '2026-01-30')

obs = OBSWorker()
arx_parser = ARXIVParser(data_range)
flx_parser = FluxiconParser(data_range)
async def sync_from_obs():
    await obs.download_all_md()
    await obs.download_metadata()

async def upload_data_obs():
    print("Подгрузка FAISS")
    await obs.upload_vectorstore()
    print("Подгрузка дайджестов")
    await obs.upload_digests()
    await obs.upload_all_md()
    await obs.put_json()


async def run():
    try:
        print("Считываем файлы из OBS")
        await sync_from_obs()
        print("Файлы на локальной машине")
    except Exception as e:
        print("Статей в объектном хранилище нет!")

    try:
        print("Работа ARXIVParser")
        await arx_parser.run() # Один парсер
    except Exception as e:
        print("У нас отвалился парсер ARXIVParser:", str(e))

    try:
        print("Работа FluxiconParser")
        await flx_parser.run() # Второй парсер
    except Exception as e:
        print("У нас отвалился парсер FluxiconParser", str(e))
    await upload_data_obs()


# if __name__ == "__main__":
#     asyncio.run(main())



