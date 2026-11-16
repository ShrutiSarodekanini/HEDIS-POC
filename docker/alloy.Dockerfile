# Alloy for the container deployment, with observability/alloy/config.alloy (tail, mask, push to Loki) copied in.
FROM grafana/alloy:v1.10.0

COPY observability/alloy/config.alloy /etc/alloy/config.alloy

CMD ["run", "--server.http.listen-addr=0.0.0.0:12345", "--storage.path=/var/lib/alloy/data", "/etc/alloy/config.alloy"]
