# import python 3.12.3 slim image from docker hub
FROM python:3.12.3-slim

# Prevent .pyc files & enable logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /sadhak

# Install system dependencies required for building Python packages and PostgreSQL client libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .

# Use --no-cache-dir to reduce image size by not caching the installed packages
RUN pip install --no-cache-dir -r requirements.txt

# copy the rest of the application code to the container
COPY . .

# Expose the port that the Django app will run on
EXPOSE 8000

# Start the Django development server
CMD ["gunicorn", "sadhak.wsgi:application", "--bind", "0.0.0.0:8000"]

