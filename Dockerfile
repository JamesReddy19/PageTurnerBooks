FROM python:3-alpine
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
ENV PORT=5000
EXPOSE 5000
CMD gunicorn --bind 0.0.0.0:"$PORT" project.wsgi:application
