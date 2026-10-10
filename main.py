import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from core.db import init_db
from web.routes import router

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from core.backup import auto_backup_if_needed
    auto_backup_if_needed()
    yield


app = FastAPI(title="Склад", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "80"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
