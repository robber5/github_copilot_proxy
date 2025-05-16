FROM python:3.9-slim

WORKDIR /app

# Install dependencies
RUN pip install aiohttp requests pydantic

# Copy the application code
COPY app /app

# Expose the port the app runs on
EXPOSE 80

# Command to run the application
CMD ["python", "/app/main.py"]