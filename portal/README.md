# Operations Portal

This directory contains the static NutsNews Operations Portal v1 shell.

The portal is read-only, has no local management buttons, and reads only the sanitized JSON snapshot generated on the VPS at `/opt/nutsnews/portal-assets/data/status.json`.

Do not commit secrets, local environment files, generated build artifacts, private keys, tokens, or runtime status snapshots.
