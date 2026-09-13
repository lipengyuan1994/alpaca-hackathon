# Vultr Docker deployment for the QQQ paper wheel

This deployment keeps the V13.5 QQQ paper wheel running when the operator Mac is
offline. The container remains paper-only and uses the same configuration,
broker-origin restriction, arm token, state hashes, and journal chain as the Mac
runtime.

## Safety boundary

Only one scheduler may be active. Installing Docker, publishing an image, or
staging an image on Vultr does not authorize the Vultr scheduler. Do not create
`/etc/alpaca-paper/enabled` until the Mac launchd job is stopped, the final state
has been copied, and read-only reconciliation succeeds on Vultr.

The image contains no Alpaca credentials or runtime state. Vultr owns:

- `/etc/alpaca-paper/secrets/alpaca/alpaca_api_key.yaml` as a read-only secret;
- `/var/lib/alpaca-paper/v13_5_qqq_market_hours_cash_secured` as durable state;
- `/opt/alpaca-paper/current-image` as the last staged immutable image identity.

## CI and delivery

Relevant pull requests build the `linux/amd64` image without publishing it. A
merge to `main` automatically publishes a commit-tagged image to GHCR with
provenance and an SBOM. The same workflow passes the immutable digest to a
deployment job, which waits for approval in the protected `vultr-paper` GitHub
environment. No operator needs to copy the digest for a normal release.

The separate manual `deploy-paper-wheel` workflow remains available for an
explicit digest rollback or repeat deployment. It uses the same protected
environment, restricted SSH path, server gates, and newest durable state.

Configure the environment with:

- variables `VULTR_DEPLOY_HOST` and `VULTR_DEPLOY_USER`;
- secret `VULTR_DEPLOY_SSH_KEY` containing a dedicated deployment key;
- secret `VULTR_SSH_KNOWN_HOSTS` containing the pinned Vultr host key;
- a required reviewer and a `main` deployment-branch restriction.

Install the generated deployment public key in the Vultr user's
`authorized_keys` with `restrict` and the forced command
`/usr/local/sbin/alpaca-paper-ssh-dispatch`. The dispatcher accepts only
`ghcr-login`, `deploy <allowed-digest>`, and `ghcr-logout`; the key cannot open
an interactive shell or forward connections.

Both automatic and manual delivery call the same deployment client and server
deployer. The deployer pulls only
`ghcr.io/lipengyuan1994/alpaca-hackathon-paper-wheel@sha256:<digest>`. While the
enable file is absent, deployment only stages the image and prints
`PAPER_WHEEL_IMAGE_STAGED_DISABLED`.

Once enabled, the deployer stops the existing Vultr container, runs read-only
broker preflight and arm verification against the new image and newest durable
state, and starts the replacement only after both gates pass. A blocked gate
attempts to restart the previously running container and leaves the staged
image identity unchanged.

Normal release sequence:

1. Push a feature branch and open a pull request.
2. Pass the repository suite and the credential-free `linux/amd64` image build.
3. Merge to `main`; GitHub publishes the exact commit image automatically.
4. Review and approve the waiting `vultr-paper` deployment job.
5. Vultr stages the digest while disabled, or runs preflight and arm verification
   before replacement once the server has been enabled.

A configuration or model change that changes the config hash will fail arm
verification until the operator completes the documented reconciliation and
audited `migrate-config` or re-arm procedure. CI/CD never creates an arm token.

## Initial host setup

Docker Engine and the Compose plugin must be installed first. From a checked-out
repository on Vultr, run:

```sh
sudo infra/paper-wheel/install-vultr.sh
```

This creates the secret and state directories and installs the Compose file and
deployer. It does not start a container.

## Cutover checklist

1. Stage the tested image digest on Vultr while the Mac remains authoritative.
2. Stop and unload the Mac launchd job.
3. Copy the final state directory without `runtime.lock`, preserving modes and
   the journal, state, and arm token.
4. Mount the paper credential bundle on Vultr and verify its owner and mode.
5. Run `preflight`, `verify-arm`, and `status` in one-shot containers.
6. Create `/etc/alpaca-paper/enabled` with the exact content `1`, then deploy the
   same digest again.
7. Verify the container is running and reconcile positions and open orders.

Rollback must deploy an older image against the newest state. Never restore an
older journal or state snapshot over newer broker activity.
