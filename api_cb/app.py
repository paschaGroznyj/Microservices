# currency_service/main.py
from fastapi import FastAPI, Query
from request_cb import fetch_rates

app = FastAPI()

@app.get("/rate")
def get_rate(
    from_curr: str = Query("USD", alias="from"),
    date: str = None  # опционально: "01/12/2025"
):
    try:
        rates = fetch_rates(date)
        rate = rates.get(from_curr.upper())
        if rate is None:
            return {"error": f"Валюта {from_curr} не найдена"}
        return {
            "from": from_curr.upper(),
            "to": "RUB",
            "rate": round(rate, 4),
            "date": date or "сегодня"
        }
    except Exception as e:
        return {"error": str(e)}

import uvicorn
from utils.config import HOST_CRB_SERVICE

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=HOST_CRB_SERVICE)
