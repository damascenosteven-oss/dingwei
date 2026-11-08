from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .rate_limit import FixedWindowRateLimiter, SensitiveEndpointRateLimitMiddleware
from .routers import auth, device, families


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or get_settings()
    application = FastAPI(
        title="家人定位 API",
        version="0.1.0",
        docs_url="/api/docs" if current_settings.expose_api_docs else None,
        openapi_url="/api/openapi.json" if current_settings.expose_api_docs else None,
        redoc_url=None,
    )
    limiter = FixedWindowRateLimiter(
        request_limit=current_settings.sensitive_rate_limit_requests,
        window_seconds=current_settings.sensitive_rate_limit_window_seconds,
    )
    application.state.sensitive_rate_limiter = limiter
    application.add_middleware(SensitiveEndpointRateLimitMiddleware, limiter=limiter)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=current_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization", "X-Bootstrap-Token"],
    )
    application.include_router(auth.router)
    application.include_router(families.router)
    application.include_router(device.router)

    @application.middleware("http")
    async def protect_api_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
        return response

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/v1/push/public-key")
    def push_public_key() -> dict[str, str]:
        return {"publicKey": current_settings.vapid_public_key}

    return application


app = create_app()
