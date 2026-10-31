# Stage 1: Build the React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build

# Stage 2: Production Python runtime with system tools
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies:
# - git: repository cloning and analysis
# - graphviz: architecture diagram rendering (dot)
# - libreoffice-impress-nogpu: slide rendering to PNG images
# - fonts: clean typography in generated slides
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    graphviz \
    libreoffice-impress-nogpu \
    libreoffice-common \
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install backend dependencies
COPY backend/pyproject.toml ./backend/
RUN pip install --no-cache-dir ./backend

# Copy backend source code and default presentation templates
COPY backend/ ./backend/
COPY templates/ ./templates/

# Copy built frontend assets so FastAPI serves the complete web app
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Default persistent storage location
RUN mkdir -p /app/storage

ENV STORAGE_DIR=/app/storage \
    PORT=8000 \
    PREVIEW_MODE=libreoffice

EXPOSE 8000

# Start Uvicorn bound to the environment-provided PORT
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
