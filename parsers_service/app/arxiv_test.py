import asyncio
from parsers.arxiv_all import main


post_body = {
    "user_name": "oppo",
    "user_query": "Найди свежие статьи по object detection и computer vision",
    "params": {
        "keywords": ["computer vision"]
    }
}


if __name__ == "__main__":
    result = asyncio.run(main(post_body))
    print(result)