FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector ./collector
COPY run.py .

ENV DATA_DIR=/data
VOLUME /data

CMD ["python", "run.py"]
