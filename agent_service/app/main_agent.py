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
text = "Сформируй дайджест по статьям связанным с AI machine learning"
response = agent.process_query(text)

# from agent_pm.pm_parser.parser import parser
# import asyncio
#
# asyncio.run(parser.run())