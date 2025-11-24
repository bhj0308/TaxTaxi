# Use official lightweight Python image
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Copy requirements to container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source code
COPY src/ ./src/
COPY data/ ./data/

# Command to run app (adjust as needed)
CMD ["python", "src/main.py"]
