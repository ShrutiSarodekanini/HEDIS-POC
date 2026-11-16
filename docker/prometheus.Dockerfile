# Prometheus for the container deployment, with observability/prometheus.deploy.yml and alerts.yml copied in.
FROM prom/prometheus:v3.5.0

COPY observability/prometheus.deploy.yml /etc/prometheus/prometheus.yml
COPY observability/alerts.yml /etc/prometheus/alerts.yml

CMD ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/prometheus", "--storage.tsdb.retention.time=15d"]
