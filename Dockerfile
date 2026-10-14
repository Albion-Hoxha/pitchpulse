FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pitchpulse ./pitchpulse
COPY static ./static

RUN useradd --create-home app
USER app

# Azure OpenAI settings (AZURE_OPENAI_*) and MCP_ALLOWED_HOSTS come from the container app's
# environment and secrets, never from the image.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn pitchpulse.server:app --host 0.0.0.0 --port ${PORT}"]
