# import re
# from typing import List, Dict
# from sentence_transformers import SentenceTransformer, util
# import torch
# import numpy as np

# Попозже раскомментим :)))))
# def parse_glossary_md(file_path: str) -> List[Dict[str, str]]:
#     """Парсит формат: > *TERM (аббревиатуры)* – описание"""
#     glossary = []
    
#     with open(file_path, 'r', encoding='utf-8') as f:
#         content = f.read()

#     pattern = r'^>\s*\*(.+?)\s*\*\s*–\s*(.+?)(?=\n\n|\Z)'
#     matches = re.finditer(pattern, content, re.MULTILINE)
    
#     for m in matches:
#         term = m.group(1).strip()        # "Process Mining (PM, РМ, Анализ процессов, процесс майнинг)"
#         definition = m.group(2).strip()   # "дисциплина на стыке data science..."
        
#         glossary.append({
#             "term": term.lower(),
#             "definition": definition.lower()
#         })
    
#     print(f"📖 Загружено терминов из глоссария: {len(glossary)}")
#     return glossary

# def find_relevant_glossary(content: str, file_path: str, top_k: int = 5) -> str:
#     model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

#     glossary = parse_glossary_md(file_path)
    
#     # Весь текст статьи
#     article_emb = model.encode(content)
    
#     # Все определения глоссария
#     glossary_defs = [item['term'] for item in glossary]
#     glossary_embs = model.encode(glossary_defs)

    
#     # Косинусное сходство
#     scores = util.cos_sim(article_emb, glossary_embs)[0]

#     # print(scores)
    
#     if isinstance(scores, torch.Tensor):
#         top_indices = torch.argsort(scores, descending=True)[:top_k]
#     else:  # numpy
#         top_indices = np.argsort(scores)[-top_k:][::-1]
    
#     # Форматируем топ-N
#     matches = []
#     for idx in top_indices:
#         if scores[idx] > 0.5:
#             item = glossary[idx]
#             matches.append(f"- {item['term']}: {item['definition']}")
    
#     return '\n'.join(matches) or "Релевантных определений не найдено."
