"""Per-domain FastAPI routers.

main.py is a thin app + lifespan + include_routers shell. Each domain
owns its own APIRouter, Pydantic models, and business logic.
"""
