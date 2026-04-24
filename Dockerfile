FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y build-essential libpq-dev gettext ffmpeg libsm6 libxext6 curl gosu && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files for WhiteNoise
RUN python manage.py collectstatic --noinput

# Ensure media directories exist
RUN mkdir -p /app/media/user_avatars

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Copy entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

EXPOSE 8004

CMD ["daphne", "-b", "0.0.0.0", "-p", "8004", "design_workflow_backend.asgi:application"]
