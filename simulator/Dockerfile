FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /sim
COPY simulate_sales.py .
RUN useradd --system --no-create-home sim
USER sim
# Credentials come from the seed script via a bind mount (see docker-compose.yml).
ENTRYPOINT ["python", "simulate_sales.py", "--credentials", "/run/seed/seed_output.json"]
CMD ["--url", "http://api:8000", "--rate", "50"]
