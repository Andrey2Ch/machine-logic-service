"""
Text2SQL Routers
================

FastAPI роутеры для Text2SQL API.
"""

from .text2sql import router
from .admin import router as admin_router
from .examples import router as examples_router

__all__ = ['router', 'admin_router', 'examples_router']
