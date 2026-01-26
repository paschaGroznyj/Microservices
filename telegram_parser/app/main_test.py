from parser import ParserTG
import asyncio

async def main():
    parser = ParserTG()

    await parser.start_client()
    await parser.init_db()

    id = parser.get_internal_chat_id_by_title("[IAD] ЦК ГенИИ")
    chat = await parser.get_all_dialogs(id)
    await parser.parsing_dialogs(chat, id)

if __name__ == "__main__":
    asyncio.run(main())
