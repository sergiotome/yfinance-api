# 1. Use a specific 'slim' version for a smaller footprint 
FROM python:3.14-slim

# 2. Set a dedicated working directory (avoiding the root '/')
WORKDIR /app

# 3. Leverage layer caching for faster builds
# Copy ONLY the requirements file first
COPY requirements.txt .

# 4. Install dependencies and clean up cache to save space 
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the application code
COPY . .

# 6. Run as a non-privileged user for better security
USER 1000

# 7. Expose the port FastAPI will run on
EXPOSE 8080

# 8. Start the server using uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]