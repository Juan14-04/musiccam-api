FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home appuser
COPY --chown=appuser:appuser . .
RUN mkdir -p /app/uploads && chown appuser:appuser /app/uploads
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "flask --app wsgi db upgrade && exec gunicorn -w 3 -b 0.0.0.0:8000 wsgi:app"]
