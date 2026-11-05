# Security

The gallery binds to `127.0.0.1` and is designed for a single local user. It does
not provide accounts. Do not expose the gallery through a public reverse proxy.

Phone upload is an explicit opt-in, separate HTTP listener bound to a selected
local adapter, on a temporary port, for up to one hour. It exposes only static
upload assets, uploads and the paired session's receipts/progress; never gallery
images, gallery APIs or the gallery CSRF token. Requests require a random bearer
token carried in the QR URL fragment, then removed from the address bar. Host and
Origin checks apply. It is plaintext HTTP: use only on a trusted private LAN,
keep the QR private, and do not forward this port to the Internet. No firewall,
network configuration or ACL is modified automatically.

Phone originals stay in the library's managed incoming directory until a committed
archive entry and exact-byte destination have been verified. Rejected, near
duplicate, interrupted and failed items are retained for manual recovery. A
successful upload receipt does not mean the classifier accepted the image.

The installer downloads public dependencies and models. Classification runs
locally; the inference child rejects network connections. The QQ variant may
download QQ image attachments through the host's configured OneBot interface.
The optional gallery planner uses QQ Agent's configured model with tag names and
counts, never image bytes.

Do not attach your image database, QQ configuration, signed attachment URLs or
credentials to public issues. When reporting a problem, include the component
version and a redacted error message.
