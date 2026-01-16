# test_bot_message.py
import requests
import json

# Тестируем endpoint напрямую
url = "http://127.0.0.1:8001/send_message"

test_message = {
        "user_id": 1,
        "message": "string",
        "chat_id": 8
}

response = requests.post(url, json=test_message)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")

# Тестируем endpoint напрямую
url = "http://127.0.0.1:8001/internal/bot_message"

test_message = {
    "chat_id": 8,
    "message": {
        "user_id": -1,
        "chat_id": 8,
        "message": "Test bot message",
        "is_from_bot": True,
        "timestamp": "2025-12-13T17:46:45"
    }
}

response = requests.post(url, json=test_message)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")