FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e .

CMD ["uvicorn", "macro_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
