# Grafana for the container deployment, with the provisioning and dashboards from observability/grafana copied in.
FROM grafana/grafana:12.1.0

COPY observability/grafana/provisioning /etc/grafana/provisioning
COPY observability/grafana/dashboards /var/lib/grafana/dashboards

# Grafana's own state lives one level down, so the grafana-data volume does not hide the dashboards above.
USER root
RUN mkdir -p /var/lib/grafana/state && chown -R 472:0 /var/lib/grafana/state
USER 472

ENV GF_PATHS_DATA=/var/lib/grafana/state \
    GF_AUTH_ANONYMOUS_ENABLED=true \
    GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/hedis-overview.json

# The admin password is a runtime setting (GRAFANA_ADMIN_PASSWORD, default admin), never part of the image.
ENTRYPOINT ["/bin/sh", "-c", "export GF_SECURITY_ADMIN_PASSWORD=\"${GRAFANA_ADMIN_PASSWORD:-admin}\"; exec /run.sh \"$@\"", "--"]
