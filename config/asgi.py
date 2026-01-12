import os
from django.core.asgi import get_asgi_application
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn

# Ensure Django settings module is set
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Django ASGI app
django_app = get_asgi_application()

# FastAPI sub-app
fastapi_app = FastAPI(title="TaxTaxi API")

@fastapi_app.get("/ca-tariff")
async def ca_tariff(hs10: str):
    return {"hs10": hs10, "duty": "0% CBSA"}

@fastapi_app.get("/usa-tariff")
async def usa_tariff(hts: str):
    return {"hts": hts, "duty": "Free USITC"}

# Compose a single ASGI application that mounts FastAPI at /api and Django at root
application = Starlette(routes=[
    Mount("/api", app=fastapi_app),
    Mount("/", app=django_app),
])

if __name__ == "__main__":
    uvicorn.run("config.asgi:application", host="127.0.0.1", port=8000, log_level="info")
