from fastapi import FastAPI

from app.api.routes import admin, analytics, health, markets, product, signals, users
from app.core.logging import setup_logging

setup_logging()
app = FastAPI(title="Prediction Market Scanner", version="0.1.0")

app.include_router(health.router)
app.include_router(markets.router)
app.include_router(signals.router)
app.include_router(product.router)
app.include_router(analytics.router)
app.include_router(users.router)
app.include_router(admin.router)
