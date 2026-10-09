import uuid
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.core.config import get_settings
from app.api.v1.router import api_v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý khởi tạo và dọn dẹp tài nguyên (DB Pool, Redis, Scheduler v.v.)."""
    settings = get_settings()
    print(f"[*] Starting {settings.APP_NAME} in [{settings.APP_ENV}] mode...")
    
    # Khởi động Scheduler Service cho thông báo nhắc hẹn
    from app.services.scheduler import scheduler_service
    scheduler_service.start()

    # Khởi động Indexing Worker để phục hồi các job gián đoạn ngay khi boot
    from app.knowledge.indexing import indexing_service
    await indexing_service.start_worker()
    
    yield
    
    print(f"[*] Shutting down {settings.APP_NAME}...")
    scheduler_service.stop()
    indexing_service.stop_worker()


def create_application() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description="Enterprise AI Agent Gateway - Decoupled Odoo & Google Workspace Assistant",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.DEBUG else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Middleware gắn X-Request-ID cho audit trail
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start_time = time.perf_counter()

        response: Response = await call_next(request)

        duration = time.perf_counter() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration:.4f}s"
        return response

    # Mount API routers
    app.include_router(api_v1_router)

    from app.api.v1.odoo_auth import router as odoo_auth_router
    app.include_router(odoo_auth_router)

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "message": "Enterprise AI Agent API Gateway is running",
            "docs": "/docs" if settings.DEBUG else "disabled"
        }

    return app


app = create_application()
