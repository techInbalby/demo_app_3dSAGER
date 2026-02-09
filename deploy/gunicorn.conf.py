import os

workers = int(os.getenv("GUNICORN_WORKERS", "4"))
threads = int(os.getenv("GUNICORN_THREADS", "2"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "180"))
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5000")

max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "50"))

accesslog = "-"
errorlog = "-"
