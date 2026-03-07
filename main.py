from fastapi import FastAPI
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from api import api_router
from core.config import config
from core.responseTypes import CustomException
from core.middlewares import include_middleware


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    formatted_response = {
        "status": "failed",
        "message": "Validation Error",
        "error_code": "validation-error",
        "data": exc.errors(),
    }
    return JSONResponse(content=formatted_response, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


async def custom_exception_handler(request: Request, exc: CustomException):
    formatted_response = {
        "status": "failed",
        "message": exc.detail,
        "error_code": exc.error_code,
        "data": exc.data,
    }
    return JSONResponse(content=formatted_response, status_code=exc.status_code)


def create_app() -> FastAPI:
    app = FastAPI(
        title=config.APP_NAME,
        version=config.APP_VERSION,
        docs_url="/docs" if config.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if config.ENVIRONMENT != "production" else None,
        openapi_url="/openapi.json" if config.ENVIRONMENT != "production" else None,
        middleware=include_middleware(),
    )

    router_prefix = "/v1/paper-book" if config.ENVIRONMENT != "production" else ""
    app.include_router(api_router, prefix=router_prefix)

    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(CustomException, custom_exception_handler)

    return app


app = create_app()
