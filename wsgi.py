"""WSGI entry point for production servers such as gunicorn.

Usage: ``gunicorn wsgi:app`` (configuration lives in gunicorn.conf.py).
"""
from app import app

__all__ = ["app"]
