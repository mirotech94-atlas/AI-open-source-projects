# The overnight sandbox's image. Everything it can reach is decided outside
# this file: the container it runs in is --read-only with only /tmp and
# /home/night writable (see atlas/modules/nightshift/sandbox.py:docker_argv),
# attached to the `nightshift` docker network, which is --internal with an
# egress proxy allow-listing registry.npmjs.org, pypi.org, api.anthropic.com
# and the git host only (see README.md in this directory — that network is a
# host action for Miro, this image never creates it).
FROM node:22-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

# Non-root: a task running inside here is an LLM's own choice of shell
# commands (invariant 4 — every ingested byte is untrusted), and the
# blast radius of it going wrong stays inside a throwaway container either
# way, but there is no reason to also hand it root.
RUN useradd --create-home --shell /bin/bash night
USER night
WORKDIR /home/night

# The entry script starts as root ONLY to hold the git token and to push the
# job's own branch; the model itself always runs as `night` with a clean
# environment (see entry.sh). util-linux's `runuser` does the drop.
USER root
COPY --chown=root:root entry.sh /entry.sh
RUN chmod 0755 /entry.sh

ENTRYPOINT ["/entry.sh"]
