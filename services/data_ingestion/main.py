from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .app.api import router as api_router
from .app import db


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
    ui_dir = project_root / "ui"
    if ui_dir.exists():
        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")

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
