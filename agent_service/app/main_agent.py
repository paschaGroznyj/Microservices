import sys
import io

# # Принудительно установить UTF-8 для stdout/stderr
# if sys.platform == "win32":
#     sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
#     sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# from agent_pm.agent import KnowledgeAgent
#
# agent = KnowledgeAgent()
#
# text = "Сформируй дайджесты по всем докам"
# response = agent.process_query(text)
# response_text = response["message"]
# print(response_text)

# from agent_pm.pm_parser.parser import parser
# import asyncio
#
# asyncio.run(parser.run())