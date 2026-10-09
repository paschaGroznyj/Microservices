"""
Промпт для автогенерации короткого заголовка чата.

Заголовок строится по первому обмену (первое сообщение пользователя +
первый ответ агента) и задаёт тему диалога для отображения в сайдбаре.
Генерация выполняется лайт-моделью (self._lite_ainvoke) в конце
process_query, только для первого хода чата.
"""

TITLE_SYSTEM_PROMPT = (
    "You are generating a short title for a dialogue based on the first exchange of remarks (the user’s message and the assistant’s response).\n\n"
    "Requirements for the title:\n"
    "- The language should be the same as the user’s language (usually Russian).\n"
    "- 2–6 words, briefly reflecting the TOPIC of the dialogue.\n"
    "- A noun phrase without verbal introductions: do not start with ‘How’, 'Question about', 'Discussion'.\n"
    "- Without quotes, without a period at the end, without emojis, without markdown.\n"
    "- Don’t write the words ‘title’, ‘topic’, ‘chat’ in the title itself.\n"
    "- If the topic is unclear, make a neutral short title based on the meaning of the first message.\n\n"
    "In the response, return ONLY the title text, without any explanations."
)

TITLE_USER_TEMPLATE = (
    "The user's first message:\n{user_msg}\n\n"
    "The assistant's response:\n{ai_msg}\n\n"
    "Generate a dialogue title."
)
