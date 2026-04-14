FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install poetry && poetry install --no-dev
EXPOSE 8765
CMD ["poetry", "run", "python", "operate/main.py"]

