FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY examples ./examples
RUN mkdir -p data/mock
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
