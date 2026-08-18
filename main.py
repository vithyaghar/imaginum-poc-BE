from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from routes.thread_routes import router as thread_router
from routes.websocket_route import router as websocket_router
from routes.pdf_routes import router as pdf_router
from routes.auth_routes import router as auth_router
from routes.drive_routes import router as drive_router
from routes.canva_routes import router as canva_router
from fastapi.middleware.cors import CORSMiddleware
from database.database import init_db

from dotenv import load_dotenv

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks before serving requests."""
    init_db()
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="."), name="static")
app.include_router(thread_router)
app.include_router(websocket_router, prefix="/api")
app.include_router(pdf_router)
app.include_router(auth_router, prefix="/api")
app.include_router(drive_router, prefix="/api")
app.include_router(canva_router, prefix="/api")