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

Pull requests build the `linux/amd64` image without publishing it. A merge to
`main` publishes a commit-tagged image to GHCR with provenance and an SBOM. The
manual `deploy-paper-wheel` workflow accepts the resulting full digest and uses
the protected `vultr-paper` GitHub environment.

Configure the environment with:

- variables `VULTR_DEPLOY_HOST` and `VULTR_DEPLOY_USER`;
- secret `VULTR_DEPLOY_SSH_KEY` containing a dedicated deployment key;
- secret `VULTR_SSH_KNOWN_HOSTS` containing the pinned Vultr host key;
- a required reviewer and a `main` deployment-branch restriction.

The deployer pulls only
`ghcr.io/lipengyuan1994/alpaca-hackathon-paper-wheel@sha256:<digest>`. While the
enable file is absent, deployment only stages the image and prints
`PAPER_WHEEL_IMAGE_STAGED_DISABLED`.

Once enabled, the deployer stops the existing Vultr container, runs read-only
broker preflight and arm verification against the new image and newest durable
state, and starts the replacement only after both gates pass. A blocked gate
attempts to restart the previously running container and leaves the staged
image identity unchanged.

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
