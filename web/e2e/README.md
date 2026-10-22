# Browser tests (Playwright)

Scripted checks of the web interface against a **live stack**. They are the repeatable form of the browser-agent
requests 10-16 in `lab-exchange`, plus a smoke test that opens every page. They are separate from the app
(`web/package.json` is untouched), so they do not change the web build, its lock file or the SBOM.

| File | Covers | Changes the lab? |
|---|---|---|
| `smoke.spec.ts` | every main page opens without a script error or a 5xx; the sticky top bar | no |
| `secrets-overdue.spec.ts` | requests 12-13: overdue warning, tab label, chips; the Rotate overdue now dialog (cancelled) | no |
| `add-host.spec.ts` | request 14: the Add host dialog (cancelled) | no |
| `drive-page.spec.ts` | request 15: drive page, ranges, links, attributes | no (`Read now` stores a reading: writes only) |
| `certificates.spec.ts` | request 16: list, filters, detail, Request and The CA tabs | no |
| `forms.spec.ts` | request 11: token dialog, New environment form checks | no (the account-save check: writes only) |
| `catalog-package.spec.ts` | request 10: a published package refuses a draft item | **writes only**: creates `zz-` catalog items |

`npm run test` runs everything that does not change the lab. `npm run test:writes` also runs the ones marked
"writes only" (it sets `E2E_ALLOW_WRITES=1`). No test ever presses Create, Destroy, Redeploy, Rotate now, Request
the certificate, Revoke or the final button of the Add host or Rotate overdue dialogs.

## Settings

| Variable | Meaning | Default |
|---|---|---|
| `LABAPE_URL` | the stack's address | required |
| `LABAPE_INSECURE_TLS` | `1` when the lab's platform CA is not trusted by the browser | off |
| `LABAPE_SESSION_COOKIE` | value of the `labape_session` cookie, for `login` without a display | unset |
| `E2E_ENV`, `E2E_HOST`, `E2E_DISK` | the environment, KVM host and drive the tests open | `lab2`, `kvm1`, `sdc` |

Lab values are in `Live-Testing.local.md`; they never go in this folder.

## Run

There is no Node on the Windows box. Either use any machine with Node 22 and a display, or run in the Playwright
container on the lab host (the image tag must match the version in `package.json`):

```bash
# 1. Sign in once. With a display:  npm ci && npx playwright install chromium && LABAPE_URL=... npm run login
#    Without one: sign in with a browser, copy the labape_session cookie (developer tools → Application → Cookies)
#    and use it once for the login project:
cd web/e2e
docker run --rm -it -v "$PWD":/e2e -w /e2e -e LABAPE_URL -e LABAPE_INSECURE_TLS=1 -e LABAPE_SESSION_COOKIE \
  mcr.microsoft.com/playwright:v1.54.2-jammy sh -c 'npm ci && npx playwright test --project=login'

# 2. Run the tests (the state/ folder keeps the session; the cookie is not needed again until it expires).
docker run --rm -v "$PWD":/e2e -w /e2e -e LABAPE_URL -e LABAPE_INSECURE_TLS=1 \
  mcr.microsoft.com/playwright:v1.54.2-jammy sh -c 'npm ci && npm run test > results.txt 2>&1; tail -n 40 results.txt'
```

Failures leave a screenshot and trace in `results/` and an HTML report in `report/` (`npm run report`).
`state/`, `results/` and `report/` are gitignored: `state/admin.json` is a live session, never commit or share it.

## Status

Run 2026-10-21 against the lab stack (Node 24 on Windows, `npm install` here, no container): **38 passed, 4 skipped**.
The skips are the three "writes only" tests plus the Rotate overdue now dialog test, which skips itself when the
dialog says nothing is overdue. Tests skip themselves when the data they need is absent (nothing overdue, no
`zz-cert-test` rows, an undeployed environment). `package-lock.json` is committed.
