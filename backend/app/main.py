from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import me

app = FastAPI(title="Dashcam Portal API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(me.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
