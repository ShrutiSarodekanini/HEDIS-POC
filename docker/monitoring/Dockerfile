# Monitoring for the container deployment: Prometheus, Loki, Alloy and Grafana in one container, so the deployment
# needs one monitoring image and one container instead of four. start.sh runs the four tools, prefixes their output
# and stops the container when one of them stops, so Docker restarts it. Grafana's Ubuntu image is the base because
# the Alloy binary needs glibc; the other binaries are copied from their pinned official images.
#   docker build -f docker/monitoring/Dockerfile .   (from the repo root)
FROM prom/prometheus:v3.5.0 AS prometheus
FROM grafana/loki:3.5.0 AS loki
FROM grafana/alloy:v1.10.0 AS alloy

FROM grafana/grafana:12.1.0-ubuntu

USER root
COPY --from=prometheus /bin/prometheus /usr/local/bin/prometheus
COPY --from=loki /usr/bin/loki /usr/local/bin/loki
COPY --from=alloy /bin/alloy /usr/local/bin/alloy

COPY docker/monitoring/prometheus.yml /etc/prometheus/prometheus.yml
COPY observability/alerts.yml /etc/prometheus/alerts.yml
COPY observability/loki/loki-config.yml /etc/loki/loki-config.yml
COPY observability/alloy/config.alloy /etc/alloy/config.alloy
COPY observability/grafana/provisioning /etc/grafana/provisioning
COPY observability/grafana/dashboards /var/lib/grafana/dashboards
COPY --chmod=755 docker/monitoring/start.sh /usr/local/bin/monitoring-start.sh

# Grafana's own state lives one level down, so the grafana-data volume does not hide the dashboards above.
RUN mkdir -p /prometheus /loki /var/lib/alloy/data /var/lib/grafana/state \
    && chown -R 472:0 /prometheus /loki /var/lib/alloy /var/lib/grafana/state
USER 472

ENV GF_PATHS_DATA=/var/lib/grafana/state \
    GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/hedis-overview.json \
    ALLOY_DEPLOY_MODE=docker

# GRAFANA_ADMIN_PASSWORD and GRAFANA_ANONYMOUS are read by start.sh when the container starts, never built in.
ENTRYPOINT ["/usr/local/bin/monitoring-start.sh"]
