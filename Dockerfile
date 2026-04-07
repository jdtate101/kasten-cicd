FROM python:3.12-alpine

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx python-multipart

COPY app/ /app/

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
