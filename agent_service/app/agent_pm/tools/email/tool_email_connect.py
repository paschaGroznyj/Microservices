from langchain.tools import tool


def make_send_by_email_tool():

    @tool
    async def work_with_email():
        """
        Работа с почтовым ящиком.   
        РАСПОЛОЖЕНИЕ МОДУЛЯ:
            /app/dev_utils/work_with_mail.py
            (в среде исполнения sandbox доступен через sys.path)

        ИМПОРТ:
            import sys
            sys.path.insert(0, "/app")
            from dev_utils.work_with_mail import MailAgent

        ИНИЦИАЛИЗАЦИЯ:
            agent = MailAgent()
        
        # Переменные заданы внутри скрипта, но ты можешь смотреть свою почту через os.getenv()
        | Переменная       | Описание                          | Дефолт |
        |------------------|-----------------------------------|--------|
        | `MAIL_HOST`      | Хост сервера (mailcow)            | —      |
        | `MAIL_PORT`      | Порт IMAPS                        | `993`  |
        | `MAIL_SMTP_PORT` | Порт SMTPS                        | `465`  |
        | `MAIL_LOGIN`     | Email-адрес отправителя           | —      |
        | `MAIL_PASSWORD`  | Пароль приложения                 | —      |

        ══════════════════════════════════════════════════════════════
        МЕТОДЫ
        ══════════════════════════════════════════════════════════════

        1. ОТПРАВКА ПИСЬМА (HTML + plain fallback)
            agent.send_email(
                to_addrs=["user@example.com"],     # список или одна строка
                subject="Тема письма",
                html_body="...",       # HTML-содержимое
                plain_body="Текстовый фолбэк"       # необязательно, авто если None
            )
        2. ОТПРАВКА HTML-ПИСЬМА (упрощённый вызов)
            agent.send_html_email(
                to_addrs="user@example.com",        # строка или список
                subject="Тема",
                html_body="..."
            )
        3. ЧТЕНИЕ ПОСЛЕДНИХ ПИСЕМ ИЗ INBOX
            emails = agent.read_inbox(limit=5)
        4. КОЛИЧЕСТВО ПИСЕМ В INBOX
            count = agent.count_inbox()
        5. СПИСОК ПАПОК
            folders = agent.list_folders()
        """
    return work_with_email