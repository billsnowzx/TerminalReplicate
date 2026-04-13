FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e .

CMD ["streamlit", "run", "src/macro_platform/ui/app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
