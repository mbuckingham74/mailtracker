FROM python:3.12-alpine

WORKDIR /app

# Install tzdata for timezone support (required by zoneinfo module)
RUN apk add --no-cache tzdata

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Create directory for GeoIP database
RUN mkdir -p /app/data/geoip

# Copy application
COPY app/ ./app/

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
