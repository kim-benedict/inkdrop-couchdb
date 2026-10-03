# inkdrop-couchdb

Private Inkdrop sync on CouchDB, with local image compression and server storage guards.

## Desktop

Install Node.js 22+ and open Inkdrop once. Clone this repository to a permanent location, then run:

```sh
npm ci --ignore-scripts
npm run install-client
```

Reload Inkdrop from **Developer → Reload**. Configure your authenticated HTTPS database URL in Inkdrop's sync settings. Repeat on each desktop; keep the checkout in place.

JPEGs are resized and compressed. PNGs use lossless compression first. Originals stay on the uploading device; the optimized copy syncs through CouchDB. See [image behavior](docs/images.md).

## Server

On a fresh Debian 12 host with Docker, Caddy, nftables, DNS, and inbound HTTPS configured:

```sh
sudo python3 server/setup.py --host notes.example.com
```

The installer prompts for separate admin, sync, and reader passwords. Use `inkdrop` for sync and `reader` for read-only integrations. CouchDB stays on loopback; Caddy provides HTTPS.

For an IPv6-only `e2-micro` deployment, follow [Google Cloud setup](docs/google-cloud.md). Free-tier eligibility is conditional. The outbound guard limits traffic; it is not a billing cap.

## Tools

```sh
node scripts/read-image.cjs file:ID image.png
npm run uninstall-client
npm test
```

Tested with Inkdrop 6.1.5 on macOS and an existing Debian 12 server. Windows/Linux desktop paths are supported in code but have not been tested on those devices. Fresh server provisioning has not been exercised end to end.
