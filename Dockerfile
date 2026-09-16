FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Taipei \
    REP0RTER_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rep0rter ./rep0rter

VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "rep0rter"]
CMD ["loop", "--interval", "3600"]
