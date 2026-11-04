FROM python:3.11-slim
WORKDIR /app
COPY training/crisis/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu
COPY backend /app/backend
COPY training /app/training
ENV PYTHONPATH=/app OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
CMD ["python", "-m", "training.crisis.train", "--help"]
