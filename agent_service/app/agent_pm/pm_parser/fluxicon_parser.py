import requests
import warnings
import json
import datetime as dt
warnings.filterwarnings("ignore")
from bs4 import BeautifulSoup


def parse_fluxicon_blog():
    """
    Парсит последние записи блога ресурса www.fluxicon.com/blog.
    Каждая запись сохраняется в md файл. Метаданные записей сохроняются в json файл.
    """

    main_url = 'https://www.fluxicon.com'

    response = requests.get(main_url + '/blog/', verify=False)
    soup = BeautifulSoup(response.text, 'html.parser')
    if response.status_code != 200:
        print(f'!!!! Внимание!  Ресурс {main_url} не доступен')
        print('response.status_code:'.upper(), response.status_code)
        return False

    # Извлечение всех записей блога
    articles = soup.find_all('div', 'entry')
    lst_dict_blogs = []  # Список метаданных статей

    if articles:
        for indx, article in enumerate(articles):
            link = article.find(class_="entry-header").a['href']
            # Извлечение даты публикации
            year_month = link.split('/')[2] + '-' + link.split('/')[3]
            day_article = article.find('span', 'entry-date').text.split(' ')[0]

            dict_meta = {
                'title': article.find('span', 'entry-header').text,
                 'date_sub': year_month + '-' + day_article,
                'url_article': main_url + link,
            }

        # Собираем все дочерние элементы, содержащие текст
            texts = []
            for child in article.children:
                if child.name == 'p':
                    texts.append(child.get_text(strip=True))
                elif child.name == 'ul':  # Обрабатываем пункты списка
                    list_items = child.find_all('li')
                    for i in list_items:
                        texts.append(f'- {i.get_text(strip=True)}')  # Маркированный список
                elif child.name == 'h2':  # Заголовки тоже включаем
                    texts.append(f'\n## {child.get_text(strip=True)}\n')

            # Объединяем собранные тексты с отступами
            full_article = '\n\n'.join(texts)


            # Сохраняем результат в Markdown-файл
            output_file = f'article_{indx}.md'
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(full_article)
            print(f'Запись сохранена в файл: {output_file}')
            dict_meta['file_article'] = output_file
            lst_dict_blogs.append(dict_meta)
            print('*' * 50)

        # Сохраняем метаданные в JSON-файл
        date_parse = dt.datetime.today().strftime('%Y%m%d')
        meta_json = f'fluxicon_{date_parse}.json'
        with open(meta_json, 'w', encoding='utf-8') as f:
            json.dump(lst_dict_blogs, f, ensure_ascii=False, indent=4)

        return lst_dict_blogs


# Запуск парсинга блога fluxicon
parse_fluxicon_blog()