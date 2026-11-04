# Production server settings: `gunicorn -c gunicorn.conf.py server:app`
import os

bind = f"0.0.0.0:{os.getenv('PORT', '4242')}"
workers = int(os.getenv("WEB_WORKERS", 2))   # SQLite is fine with a couple of processes
threads = 4
timeout = 60
accesslog = "-"
