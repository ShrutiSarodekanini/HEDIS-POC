from __future__ import annotations

import datetime as dt
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .app.api import router as api_router
from .app import db

NPM_BIN = "npm.cmd" if sys.platform.startswith("win") else "npm"


def run_backend_cli(args: list[str]) -> int:
    """Backwards-compatible wrapper to the existing TS backend CLI."""
    project_root = Path(__file__).resolve().parents[2]
    cmd = [NPM_BIN, "run", "backend:cli", "--", *args]
    completed = subprocess.run(cmd, cwd=project_root)
    return completed.returncode


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
        return {
            "status": "ok",
            "message": "Service reachable",
            "version": "1.0.0",
            "checkedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    return app


app = create_app()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Preserve legacy wrapper behavior for direct script execution with CLI args.
        sys.exit(run_backend_cli(sys.argv[1:]))

    import uvicorn

    uvicorn.run("services.data_ingestion.main:app", host="0.0.0.0", port=8000, reload=True)
