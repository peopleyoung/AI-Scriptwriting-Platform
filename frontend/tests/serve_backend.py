"""Run browser acceptance tests against a disposable database."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

with TemporaryDirectory(prefix="mujian-e2e-") as directory:
    os.environ["APP_DATABASE_URL"] = f"sqlite:///{directory}/browser.db"
    os.environ["APP_BACKUP_DIR"] = f"{directory}/backups"
    os.environ["APP_BOOTSTRAP_ADMIN_USERNAME"] = ""
    os.environ["APP_BOOTSTRAP_ADMIN_PASSWORD"] = ""
    import uvicorn
    from app.config import Settings
    from app.main import create_app

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
    from http_model_server import model_server

    with model_server(8016):
        uvicorn.run(create_app(Settings(database_url=os.environ["APP_DATABASE_URL"], backup_dir=os.environ["APP_BACKUP_DIR"], bootstrap_admin_username=None, bootstrap_admin_password=None, bootstrap_secret=None, cookie_secure=False)), host="127.0.0.1", port=8015, log_level="warning")
