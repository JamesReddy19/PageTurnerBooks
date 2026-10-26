FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The SQLite database (instance/) and uploaded covers (static/uploads/) are
# created on demand at runtime; these directories are the mount points to use
# if a persistent disk is attached.
RUN mkdir -p instance static/uploads

EXPOSE 5000

# gunicorn.conf.py binds 0.0.0.0 and reads the platform-supplied PORT.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
