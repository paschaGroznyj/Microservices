# currency_service/cbr_parser.py
import xml.etree.ElementTree as ET
from datetime import datetime
import requests


def fetch_rates(date_str: str = None):
    """
    Получает и парсит курсы с ЦБ РФ.
    date_str: формат 'DD/MM/YYYY' (например, '01/12/2025')
    """
    if not date_str:
        date_str = datetime.now().strftime("%d/%m/%Y")

    url = f"https://www.cbr.ru/scripts/XML_daily.asp?date_req={date_str}"
    response = requests.get(url)
    response.encoding = 'cp1251'  # важно!

    if response.status_code != 200:
        raise Exception(f"Ошибка ЦБ: {response.status_code}")

    root = ET.fromstring(response.text)
    rates = {}

    for valute in root.findall('Valute'):
        char_code = valute.find('CharCode').text
        nominal = int(valute.find('Nominal').text)
        value = float(valute.find('Value').text.replace(',', '.'))
        rate = value / nominal
        rates[char_code] = rate

    return rates
