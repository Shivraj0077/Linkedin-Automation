FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY ENDPOINT_MAP.md ./

EXPOSE 8000

# Credentials are injected at runtime via environment variables
# (LINKEDIN_LI_AT, LINKEDIN_JSESSIONID) -- never baked into the image.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
