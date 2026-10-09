import os

import uvicorn

from servio.app import create_app

uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
