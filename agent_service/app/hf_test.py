import os

from huggingface_hub import InferenceClient
from dotenv import load_dotenv

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN")

client = InferenceClient(api_key=HF_TOKEN)
response = client.chat.completions.create(
    model="Qwen/Qwen3-VL-8B-Instruct",
    messages=[{"role": "user", "content": "Привет"}],
    response_format={"type": "json_object"}
)
response = response.choices[0].message.content
with open("answer.txt", mode='w', encoding='utf-8') as file:
    file.write(response)