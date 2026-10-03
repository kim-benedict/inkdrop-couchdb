# Images

Inkdrop stores images as CouchDB attachments, referenced by `inkdrop://file:ID`. A filesystem path alone is not a synced image. Insert an image through Inkdrop, rather than linking a local file.

The desktop hook runs new image writes in a separate Node process before saving them. It leaves existing revisions and sync replication unchanged.

| Format | Behavior |
| --- | --- |
| JPEG | Correct orientation; max edge 2560 px initially; quality 85, then smaller presets if needed. Only use an output smaller than the source. |
| PNG | Keep alpha and losslessly recompress first. If still over 2 MiB, resize to 2560 or 2048 px. |
| GIF / APNG / high bit-depth PNG | Preserve bytes and animation; reject above 2 MiB. |
| SVG / HEIC / HEIF | Preserve bytes; reject above 2 MiB. Display support depends on the client. |

Output is capped at 2 MiB. The worker accepts sources up to 32 MiB and 40 megapixels for decoded raster images; Inkdrop's file importer can reject files above its own 10 MiB limit before the hook runs. Clipboard images use the worker directly. Unsupported or uncompressible images fail visibly.

Originals are archived by SHA-256 under `image-originals/` in the Inkdrop config directory, before transformation. This folder is private and **does not sync**. Back it up separately if you need originals on another device. The archive grows over time; compression requires at least 1 GiB of local free space beyond the input. Nothing automatically deletes originals or rewrites historical attachments.

The hook tolerates an unreachable server so Inkdrop can work offline. A reported database size of 8 GiB stops new image saves. Server controls apply independently:

- 4 MiB maximum attachment, including uploads from clients without the hook.
- Writes paused below 6 GiB free space or at 8 GiB allocated database storage; resume above 7 GiB free and below 7.5 GiB used.
- Storage check every minute. Reads and login remain available during a write pause.
- CouchDB disk monitoring and compaction with one job per channel. Compaction needs substantial extra disk space.

These checks reduce risk; concurrent writes between polls can cross a threshold. Disk capacity, hardware, and network availability still limit the service.

To verify a new device, attach a small PNG, wait for sync, open it on the second device, and compare the server attachment:

```sh
node scripts/read-image.cjs file:ID downloaded.png
```

The command uses the local Inkdrop login, verifies the attachment digest, and never overwrites the destination. A standalone browser or AI service must authenticate to fetch the attachment; never embed a password in public URLs. For a public blog, export selected notes and image copies to the blog host instead of publishing the private database.

References: [Inkdrop files](https://developers.inkdrop.app/data-access/files), [CouchDB attachments](https://docs.couchdb.org/en/stable/api/document/attachments.html), [disk monitoring](https://docs.couchdb.org/en/stable/config/disk-monitor.html), [compaction](https://docs.couchdb.org/en/stable/maintenance/compaction.html).
