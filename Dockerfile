FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . .

# Persistent state lives on the mounted volume
ENV STRATEGY_FACTORY_DATA_DIR=/data
ENV STRATEGY_FACTORY_REPORT_DIR=/data/reports

# Seed the database + generate initial dashboard on container start if empty,
# then hand off to gunicorn.
CMD ["sh", "-c", "python3 entrypoint.py && gunicorn dashboard_server:app --bind 0.0.0.0:${PORT:-8765} --workers 1 --threads 4 --timeout 240 --access-logfile -"]

EXPOSE 8765
