FROM python:3.13-slim

WORKDIR /app

# System deps kept minimal; pypdfium2/pillow/reportlab ship manylinux wheels.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code + the assets the server needs at runtime.
COPY app ./app
COPY web ./web
COPY assets/f1040_2025.pdf ./assets/f1040_2025.pdf
COPY testdata/w2_images/01_single_40k_baseline.png ./testdata/w2_images/01_single_40k_baseline.png

ENV PORT=8000
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
