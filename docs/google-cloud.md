# Google Cloud

This recipe uses one `e2-micro`, a 30 GB `pd-standard` disk, and IPv6 without a public IPv4 address. Confirm current [Free Tier eligibility](https://cloud.google.com/free/docs/free-cloud-features) and existing usage across your billing account before creating resources. Budgets send alerts; they do not stop charges. Google can change the program.

## Create the host

In a billing-enabled project with Compute Engine and IAP available:

1. Create a custom VPC and a dual-stack subnet in `us-west1`, `us-central1`, or `us-east1`. Use external IPv6, for example with private IPv4 range `10.10.0.0/24`.
2. Create **one** Debian 12 `e2-micro` in that subnet with a **30 GB standard persistent disk**. Set dual stack, external IPv6, and **no external IPv4**. Disable the service account, Ops Agent, and automatic snapshots. Enable OS Login and block project-wide SSH keys.
3. Permit TCP 22 only from IAP `35.235.240.0/20`. Permit TCP 80 and 443 over IPv6 for the proxy and certificate issuance. Do not expose 5984. Grant your administrator OS Login/IAP access as required by your project; do not grant database integrations Google project access.
4. Point an AAAA record at the VM's external IPv6. If clients lack IPv6, use a compatible DNS proxy, such as Cloudflare's free proxy, with **Full (strict)** TLS. Certificate validation and authenticated database requests must pass through it. Check its current limits and terms separately.

The repository does not create projects, enable billing, change IAM, or provision VMs automatically. Inspect the final VM configuration and estimate in the console.

## Install

Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) locally and complete `gcloud auth login`. Set non-secret connection details:

```sh
export INKDROP_PROJECT=your-project-id
export INKDROP_ZONE=us-west1-b
export INKDROP_VM=inkdrop-couchdb
scripts/ssh.sh
```

On the fresh VM, install prerequisites. Verify package repositories are reachable over IPv6; do not add a paid IPv4 address or NAT to work around a failed download.

```sh
sudo apt-get update
sudo apt-get install -y docker.io caddy nftables python3
sudo systemctl enable --now docker caddy
```

Copy only the server source from the local checkout:

```sh
tar -czf inkdrop-server.tar.gz server
gcloud compute scp inkdrop-server.tar.gz "$INKDROP_VM:~/" \
  --project="$INKDROP_PROJECT" --zone="$INKDROP_ZONE" --tunnel-through-iap
scripts/ssh.sh
```

On the VM:

```sh
tar -xzf inkdrop-server.tar.gz
sudo python3 server/setup.py --host notes.example.com
```

Use the `inkdrop` credentials in Inkdrop's HTTPS sync URL. URL-encode special characters when forming the URL; do not place it in shell history or this repository. The `reader` account can read notes and attachments but cannot change database documents. Keep the admin password separately for loopback administration through SSH.

## Limits and operation

CouchDB has a 600 MiB memory cap. The installer does not resize disks, add swap, or configure paid monitoring. On a 1 GiB host, optionally provision a 1 GiB swapfile on the existing disk before installation; account for it in disk headroom.

The nftables guard reserves 4 MiB chunks in a persistent UTC-month ledger: 64 MiB in the installation month and 256 MiB thereafter. Restarts do not refund reserved bytes. Corrupt state fails closed. At exhaustion, external replies stop until the next month while IAP management remains available through private IPv4. Reads stop too; uninterrupted access cannot be promised.

The guard covers public output and forwarding on the VM, not all billable Google products. Bootstrap traffic occurs before it is installed. It excludes private address ranges and is not a provider-enforced billing limit. Never delete the ledger to restore access or add a second instance under the assumption it is free.

```sh
scripts/ssh.sh 'sudo systemctl is-active caddy inkdrop-egress inkdrop-storage-guard.timer'
scripts/ssh.sh 'sudo cat /var/lib/inkdrop/image-safety/status.json'
```

If installation stops, it retains data and configuration rather than deleting or restarting from scratch. Diagnose the named error on the VM; do not rerun setup against `/srv/inkdrop`. Back up notes and originals independently: sync is not a versioned backup.
