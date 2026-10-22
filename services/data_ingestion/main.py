from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .app.api import router as api_router
from .app import db


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise


def create_app() -> FastAPI:
    app = FastAPI(title="HEDIS CSV to FHIR R4 Pipeline", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    db.Base.metadata.create_all(bind=db.engine)
    app.include_router(api_router)

    project_root = Path(__file__).resolve().parents[2]
    ui_dir = project_root / "ui" / "build"
    if ui_dir.exists():
        app.mount("/ui", SPAStaticFiles(directory=str(ui_dir), html=True), name="ui")

        @app.get("/")
        def root_redirect():
            return RedirectResponse(url="/ui/")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.data_ingestion.main:app", host="0.0.0.0", port=8000, reload=True)
