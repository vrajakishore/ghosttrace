"""Run:  python -m services.api   (set GT_PORT to change the port; default 8080)"""
import os

from services.api.server import serve

if __name__ == "__main__":
    serve(port=int(os.environ.get("GT_PORT", "8080")))
