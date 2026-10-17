#!/usr/bin/env bash
# sync-redirects.sh <labape-hostname> [alt-names]
# Make the LABaPe provider's redirect URIs match the names people reach LABaPe by (design §58): one strict
# URI per name, https://<name>/api/auth/oidc/callback, the main name first. alt-names is comma-separated
# (LABAPE_ALT_NAMES). Run by deploy.sh, and by container/caddy/web-tls.sh when the names change.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [ -z "${COMPOSE:-}" ]; then
  if command -v docker >/dev/null 2>&1; then COMPOSE="docker compose"; else COMPOSE="podman compose"; fi
fi
main="${1:?usage: sync-redirects.sh <labape-hostname> [alt-names]}"
alt="${2:-}"
$COMPOSE exec -T -e "NAMES=$main,$alt" worker ak shell -c "
import dataclasses, os
from authentik.providers.oauth2.models import OAuth2Provider
p = OAuth2Provider.objects.get(client_id='labape')
urls = []
for n in os.environ['NAMES'].split(','):
    u = 'https://%s/api/auth/oidc/callback' % n.strip().lower()
    if n.strip() and u not in urls:
        urls.append(u)
if [u.url for u in p.redirect_uris] != urls:
    p.redirect_uris = [dataclasses.replace(p.redirect_uris[0], url=u) for u in urls]
    p.save()
    print('authentik-test: the redirect URIs are now', ', '.join(urls))
" 2>&1 | grep '^authentik-test:' || true
