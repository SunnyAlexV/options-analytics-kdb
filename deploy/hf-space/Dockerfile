# Public demo of the BTC options desk: the live dashboard replaying a recorded bundle.
# No kdb+, no C++ and no licence: the bundle already holds every table the system produced.
FROM python:3.12-slim
RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
USER user
ENV BUNDLE=bundle SPEED=10 PYTHONUNBUFFERED=1
EXPOSE 7860
# one worker: the replay clock lives in the process; threads serve concurrent viewers
CMD ["gunicorn", "dashboard.demo:server", "-b", "0.0.0.0:7860", "--workers", "1", "--threads", "8", "--timeout", "120"]
