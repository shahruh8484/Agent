import logging
import os

import uvicorn

from amzagent.web.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
