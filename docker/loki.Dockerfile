# Loki for the container deployment, with observability/loki/loki-config.yml copied in.
FROM grafana/loki:3.5.0

COPY observability/loki/loki-config.yml /etc/loki/loki-config.yml

CMD ["-config.file=/etc/loki/loki-config.yml"]
