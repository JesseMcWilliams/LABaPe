# Sign-on between linked stacks (design §47.2, F1b): the Authentik objects, made by the host's
# update agent with `ak shell` inside the Authentik worker (deploy/update-agent/labape-update.sh,
# action authentik_sso). Parameters come in the environment, SSO_*; one line starting with
# "sso:" says what was done.
#   provider_add    SSO_CLIENT_ID SSO_NAME SSO_REDIRECT_URI: the client a linked stack's source uses
#                   (public, PKCE, that one redirect; the LABaPe provider's flows and mappings)
#   provider_remove SSO_CLIENT_ID
#   source_add      SSO_SLUG SSO_NAME SSO_CLIENT_ID SSO_ISSUER SSO_AUTH_BASE: "Sign in with <name>",
#                   users named <user>@<name>, groups linked by name, on the identification stage
#   source_remove   SSO_SLUG
import os

from authentik.core.models import Application
from authentik.flows.models import Flow
from authentik.providers.oauth2.models import OAuth2Provider, RedirectURI, RedirectURIMatchingMode
from authentik.sources.oauth.models import OAuthSource, OAuthSourcePropertyMapping
from authentik.stages.identification.models import IdentificationStage

E = os.environ
op = E["SSO_OP"]
IDENT = "default-authentication-identification"

if op == "provider_add":
    base = OAuth2Provider.objects.get(client_id="labape")
    p, _ = OAuth2Provider.objects.update_or_create(client_id=E["SSO_CLIENT_ID"], defaults={
        "name": f"LABaPe link: {E['SSO_NAME']}",
        "client_type": "public",
        "redirect_uris": [RedirectURI(RedirectURIMatchingMode.STRICT, E["SSO_REDIRECT_URI"])],
        "authorization_flow": base.authorization_flow,
        "invalidation_flow": base.invalidation_flow,
        "signing_key": base.signing_key,
    })
    p.property_mappings.set(base.property_mappings.all())
    Application.objects.update_or_create(slug=E["SSO_CLIENT_ID"], defaults={
        "name": f"LABaPe link: {E['SSO_NAME']}", "provider": p})
    print(f"sso: provider {p.client_id} for {E['SSO_NAME']}, redirect {E['SSO_REDIRECT_URI']}")
elif op == "provider_remove":
    Application.objects.filter(slug=E["SSO_CLIENT_ID"]).delete()
    n = OAuth2Provider.objects.filter(client_id=E["SSO_CLIENT_ID"]).delete()[0]
    print(f"sso: provider {E['SSO_CLIENT_ID']} removed ({n} object(s))")
elif op == "source_add":
    name, base, issuer = E["SSO_NAME"], E["SSO_AUTH_BASE"].rstrip("/"), E["SSO_ISSUER"].rstrip("/") + "/"
    s, _ = OAuthSource.objects.update_or_create(slug=E["SSO_SLUG"], defaults={
        "name": name,
        "provider_type": "openidconnect",
        "enabled": True,
        "consumer_key": E["SSO_CLIENT_ID"],
        "consumer_secret": "",
        "pkce": "S256",
        "authorization_code_auth_method": "post_body",
        "authorization_url": f"{base}/application/o/authorize/",
        "access_token_url": f"{base}/application/o/token/",
        "profile_url": f"{base}/application/o/userinfo/",
        "oidc_well_known_url": f"{issuer}.well-known/openid-configuration",
        "oidc_jwks_url": f"{issuer}jwks/",
        "additional_scopes": "",
        "user_matching_mode": "identifier",
        "group_matching_mode": "name_link",          # question 156 (b): the role groups carry over
        "authentication_flow": Flow.objects.get(slug="default-source-authentication"),
        "enrollment_flow": Flow.objects.get(slug="default-source-enrollment"),
    })
    m, _ = OAuthSourcePropertyMapping.objects.update_or_create(
        name=f"LABaPe link: {name} user names", defaults={
            "expression": "return {'username': '%s@%s' % (info.get('preferred_username') or info.get('nickname') "
                          "or info.get('sub'), " + repr(name) + ")}"})
    s.user_property_mappings.set([m])
    IdentificationStage.objects.get(name=IDENT).sources.add(s)
    print(f"sso: source {s.slug} (sign in with {name}) on {IDENT}")
elif op == "source_remove":
    s = OAuthSource.objects.filter(slug=E["SSO_SLUG"]).first()
    if s is not None:
        names = [m.name for m in s.user_property_mappings.all()]
        s.delete()
        OAuthSourcePropertyMapping.objects.filter(name__in=names).delete()
    print(f"sso: source {E['SSO_SLUG']} removed")
else:
    raise SystemExit(f"sso: unknown op {op}")
