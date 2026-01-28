from pathlib import Path
import re

md_file = Path("2026-01-16_Industry-Aligned_Granular_Topic_Modeling.md")
content = []

base_path = Path(__file__).resolve().parent
md_file = base_path / 'knowledge_base' / 'articles' / md_file
print(md_file)

if md_file and md_file.exists():
    print("Найден md файл", md_file)
    with open(md_file, 'r', encoding='utf-8') as f:
        md_content = f.read()
        # Убираем YAML фронтматтер
        md_content = re.split(r'^---\s*$', md_content, maxsplit=2)[2] if '---' in md_content else md_content
        content.append(re.sub(r'^#{1,6}\s+', '### ', md_content, flags=re.MULTILINE))

content.append("---" * 30)
print(content[0][:100])