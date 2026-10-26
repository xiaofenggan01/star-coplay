from fastapi import FastAPI

from app.v1 import router as v1_router

app = FastAPI(title="star-api")


@app.get("/ping")
def ping():
    return {"ok": True}


app.include_router(v1_router, prefix="/v1")
