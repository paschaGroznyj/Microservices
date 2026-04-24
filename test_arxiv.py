import asyncio
from parsers_service.app.parsers.arxiv_all_search import main


post_body = {
    "user_name": "alexey",
    "user_query": "Найди свежие статьи по object detection и computer vision",
    "params": {
        "keywords": ["computer vision", "object detection"]
    }
}


if __name__ == "__main__":
    result = asyncio.run(main(post_body))
    print(result)