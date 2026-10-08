FROM python:3.11-slim

LABEL maintainer="Dipen Chawla"
LABEL description="AcuityBridge Safety & Escalation Orchestrator -- Synthetic Demo"

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir pydantic pyyaml

# Copy application code
COPY acuitybridge/ acuitybridge/
COPY examples/ examples/

# Run the synthetic scenario demo
CMD ["python", "examples/synthetic_scenario.py"]
