import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "familychef.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        # Behind Caddy on the docker network: trust X-Forwarded-Proto so cookies get "Secure".
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
