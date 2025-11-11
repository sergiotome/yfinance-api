# Use official lightweight Python image
FROM python:3

# Set working directory
WORKDIR /

# Copy project files
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Expose the port FastAPI will run on
EXPOSE 8080

# Start the server using uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
