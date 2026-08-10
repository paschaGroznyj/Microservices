import os
import smtplib
import imaplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate
from email import message_from_bytes
from email.header import decode_header


class MailAgent:
    """Класс для работы с почтой через IMAP (приём) и SMTP (отправка)."""

    def __init__(self):
        self.host = os.environ.get("MAIL_HOST")
        self.imap_port = int(os.environ.get("MAIL_PORT", "993"))
        self.smtp_port = int(os.environ.get("MAIL_SMTP_PORT", "465"))
        self.login = os.environ.get("MAIL_LOGIN")
        self.password = os.environ.get("MAIL_PASSWORD")

        if not all([self.host, self.login, self.password]):
            raise RuntimeError(
                "Не заданы переменные окружения: MAIL_HOST, MAIL_LOGIN, MAIL_PASSWORD."
            )

    def send_email(self, to_addrs, subject, html_body, plain_body=None):
        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]
        if plain_body is None:
            plain_body = "Это письмо в HTML-формате."

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.login
        msg["To"] = ", ".join(to_addrs)
        msg["Date"] = formatdate(localtime=False)
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        smtp = smtplib.SMTP_SSL(self.host, self.smtp_port)
        try:
            smtp.login(self.login, self.password)
            smtp.sendmail(self.login, to_addrs, msg.as_string())
        finally:
            smtp.quit()
        return {"status": "ok", "from": self.login, "to": to_addrs, "subject": subject}

    def send_html_email(self, to_addrs, subject, html_body):
        return self.send_email(to_addrs, subject, html_body)

    def read_inbox(self, limit=10):
        imap = imaplib.IMAP4_SSL(self.host, self.imap_port)
        try:
            imap.login(self.login, self.password)
            imap.select("INBOX")
            status, data = imap.search(None, "ALL")
            ids = data[0].split()
            result = []
            for uid in ids[-limit:]:
                status, msg_data = imap.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                msg = message_from_bytes(raw)
                subject_parts = decode_header(msg["Subject"] or "")
                subject = ""
                for part, enc in subject_parts:
                    if isinstance(part, bytes):
                        subject += part.decode(enc or "utf-8", errors="replace")
                    else:
                        subject += part
                body_preview = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_preview = payload.decode(
                                    part.get_content_charset() or "utf-8", errors="replace"
                                )[:200]
                                break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body_preview = payload.decode(
                            msg.get_content_charset() or "utf-8", errors="replace"
                        )[:200]
                result.append({
                    "uid": uid.decode() if isinstance(uid, bytes) else uid,
                    "subject": subject,
                    "from": msg["From"],
                    "date": msg["Date"],
                    "body_preview": body_preview,
                })
            return list(reversed(result))
        finally:
            imap.logout()

    def count_inbox(self):
        imap = imaplib.IMAP4_SSL(self.host, self.imap_port)
        try:
            imap.login(self.login, self.password)
            imap.select("INBOX")
            status, data = imap.search(None, "ALL")
            return len(data[0].split())
        finally:
            imap.logout()

    def list_folders(self):
        imap = imaplib.IMAP4_SSL(self.host, self.imap_port)
        try:
            imap.login(self.login, self.password)
            status, folders = imap.list()
            result = []
            for f in folders:
                decoded = f.decode() if isinstance(f, bytes) else f
                parts = decoded.split('"/"')
                name = parts[-1].strip().strip('"') if len(parts) > 1 else decoded
                result.append(name)
            return result
        finally:
            imap.logout()

