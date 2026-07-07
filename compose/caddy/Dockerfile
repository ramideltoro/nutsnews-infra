ARG CADDY_VERSION=2.10.0
ARG CADDY_RATELIMIT_VERSION=16aecbb

FROM caddy:${CADDY_VERSION}-builder AS builder

ARG CADDY_RATELIMIT_VERSION
RUN xcaddy build \
    --with github.com/mholt/caddy-ratelimit@${CADDY_RATELIMIT_VERSION}

FROM caddy:${CADDY_VERSION}-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

USER caddy
