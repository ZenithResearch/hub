"""Entry point for the Vault API service."""
import uvicorn

from .app import create_app
from .config import VaultEntry, settings

app = create_app(VaultEntry())

if __name__ == "__main__":
    uvicorn.run(
        "services.vault_api.main:app",
        host="0.0.0.0",
        port=settings.vault_http_port,
        reload=False,
    )
