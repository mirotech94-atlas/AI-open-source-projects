# Guardian sensor.
#
# The sensor watches the LAN it runs on (ARP sweep, port scan, mDNS) and can
# serve DNS on :53, so it must share the host's network namespace:
#
#   docker build -t guardian-sensor ./sensor
#   docker run -d --name guardian-sensor --network host --cap-add NET_RAW \
#     -v /var/lib/guardian:/var/lib/guardian guardian-sensor
#
#   RUN_DNS=1  also starts the filtering resolver in the same container.
FROM python:3.12-slim

# arp/ping/ssh/whois are shelled out to by the collectors
RUN apt-get update && apt-get install -y --no-install-recommends \
        iputils-ping net-tools iproute2 openssh-client whois ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY guardian_sensor ./guardian_sensor
RUN pip install --no-cache-dir .

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV GUARDIAN_DATA=/var/lib/guardian \
    PYTHONUNBUFFERED=1
VOLUME ["/var/lib/guardian"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["run"]
