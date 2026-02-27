import sys
import io

# # Принудительно установить UTF-8 для stdout/stderr
# if sys.platform == "win32":
#     sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
#     sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from agent_pm.agent import KnowledgeAgent

agent = KnowledgeAgent()

# text = "Можешь найти информацию в документации о калибровке решений для LLM?"
# text = "Перестрой векторную базу"
# text = "Суммаризуй все"Ф
# text = "Сформируй дайджест по статьям за январь 2026"
# text = "Сформируй дайджесты по всем статьям"
# text = "Сформируй дайджест по этому файлу 2026-01-28_MuRAL-CPD_Active_Learning_for_Multiresolution_Change_Point_Detection.md"
# text = "Найди информацию по теме методы обработки изображений"
text = "Поиск документов с примерами оптимизации моделей"
# text = "Перестрой индексы"
# text = "Сформируй дайджесты по категории Общие принципы"
response = agent.process_query(text)
with open("answer.txt", mode='a', encoding='utf-8') as f:
    f.write(response['message'] + "\n")
#
# async def main():
#     await agent.make_reviews()
#
# if __name__ == '__main__':
#     import asyncio
#     asyncio.run(main())