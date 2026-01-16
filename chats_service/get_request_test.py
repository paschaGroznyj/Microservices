import requests
import json

# Тестируем endpoint напрямую
url = "http://localhost:8001/chats"

test_message = {
        "user_id": 1,
        "message": "string",
        "chat_id": 8
}

response = requests.get(url, params={"user_id": "3"})
print(f"Status: {response.status_code}")
print(f"Response: {response.content}")

