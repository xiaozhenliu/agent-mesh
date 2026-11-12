# Protocol reference

**English** | [简体中文](PROTOCOL.zh-CN.md)

Reference for the [agent-mesh guide](README.md): how lost mail is recovered, how each message goes to one turn, and the signed message formats with their test vectors. [How it works](README.md#how-it-works) gives the overview; the labels `[LOCAL]`, `[BOT-CLOUD]` and `[GROK-UI]` are explained under [Execution location labels](README.md#execution-location-labels).

## Recovery and receipts

The relay keeps forwarded bodies for seven days. If `fetch` cannot retrieve one, it requests a signed re-forward, at most twice per message. This also recovers a failed initial forward. The cloud also sends the relay a signed receipt (RN-GOT) saying which messages it stored and which were handled; a reply with `reply_to` counts as handled too. A message stays pending on the relay until it is handled. With no stored receipt `GOT_TIMEOUT_SECONDS` (default 15 minutes) after its doorbell, the relay re-sends the body and rings again; stored but not handled after `GOT_DONE_SECONDS` (default 2 hours), it only rings again, and the cloud hands the message out once the earlier turn's claim has expired. At most `GOT_MAX_RERINGS` (2) re-rings per message, not counted against the re-forward limit; after that the relay sends Grok Bot an alert naming the message and repeats it until the Bot confirms it. `GOT_TIMEOUT_SECONDS=0` turns re-ringing off for new mail. Known limitations:

- If the cloud stops sending receipts (an older cloud script or `RN_GOT=0`), the relay notices after one extra re-ring, sends one alert, and stops watching new mail until the next receipt arrives. Until then, a turn that stops after claiming new mail and before handling it is recovered only by the orphan scan or a resend. Messages already pending are still re-rung until handled or reported one by one.
- While receipts are off, only a reply proves a message was handled, so one that was only marked `done` ends with an alert: a false alarm, nothing is lost.
- An alert delivered `GOT_MAX_RERINGS + 1` times without being confirmed is dropped and stays only in the relay log (`ALERT` lines).
- Past 200 alerts waiting, only their count is sent; the individual alerts are in the relay log.
- Re-ringing gives up after `GOT_MAX_RERINGS` and reports the message; it never retries forever.
- The relay remembers the ids it handled for `FWD_COPY_DAYS` + 1 days (8 by default); the same id delivered again later is forwarded again.
- Startup recovery (messages the engine marked read before the relay handled them) and the cloud's orphan scan pick messages by the sender's timestamp. A message from a sender whose clock runs behind can fall outside their windows (the relay's starts at its first start on this version or 7 days back, whichever is later; the orphan scan covers `RN_ORPHAN_DAYS`, 7 by default); it then comes back only if someone resends it.
- If the relay cannot read the engine database, it skips startup recovery, logs the error and sends Grok Bot one notice per relay process; messages marked read just before the relay stopped may then not be forwarded.

## Claims and interrupted turns

Wake-ups can overlap, and a retried webhook can ring twice for the same id. `fetch` claims each body it hands out for one hour (`RN_CLAIM_LEASE`), so an overlapping turn gets `claimed` and skips it; a finished message reports `handled`. The Bot runs `rn-cloud.sh working <id>` before acting, and `rn-cloud.sh send` marks a reply as started before the engine sees it. If a turn stops after either point without recording completion, later turns report `uncertain`: the Bot checks its thread notes or asks the user rather than acting again. Every `fetch` also hands out verified bodies from the last seven days that no doorbell asked for and nobody handled, such as after a lost doorbell (`"orphan": true`; `RN_ORPHAN_DAYS` sets the days and `RN_ORPHANS=0` turns this off). This is not exactly-once: a message whose turn stopped before `working` is handed out again after the claim expires, and one whose turn stopped later waits for the user's decision.

Known limitations:

- Message folders that existed before claims were introduced are marked `legacy` on the first run after the upgrade: the orphan scan skips them, and they are handed out only when a doorbell asks for them.
- A message that fails verification (`mismatch`) is reported once by the scans no doorbell asked for (the orphan scan and `wait`/`fetch` without ids). Ids asked for explicitly, and the diagnostic `scan`, report it every time, so a lasting id collision is reported again on each re-ring.
- A scan records a bad message before printing it; a run that stops in between loses that one notice. The message stays in the engine database, and the diagnostic `scan` still shows it.
- The upgrade step does not record bad messages already there, so the first scan after the upgrade reports each of them once.

## Message formats

The examples use the default Bot address name. With another name, replace `bot`, `bot-cloud` and `bot-inbox` by `<name>`, `<name>-cloud` and `<name>-inbox`.

### Signing rules

- The key is the 64-character hex **string** in `outbound.key`, stripped of surrounding whitespace. Use its UTF-8 bytes directly as the HMAC-SHA256 key; do not hex-decode it.
- Signatures are lowercase hex.
- The engine trims message whitespace, so strip trailing body whitespace before signing.
- Avoid the literal `<reason-message` in a body.
- Protocol fields and test-vector text are identical in every language.
- All test vectors below use the same key: `0123456789abcdef` repeated four times.

### RN-OUT v1

`bot-cloud` asks `bot` to send a message:

```text
RN-OUT v1
to: <recipient address>
reply_to: <message id being replied to, or empty>
ts: <Unix seconds>
nonce: <random hex, 12 random bytes recommended; derived as below when reply_to is set>
sig: <hex>
---
<body>
```

The relay sends only to a plain address `<person>/<session>[@<machine>]`: each part is lowercase letters, digits and hyphens and starts with a letter or digit. A non-empty `reply_to` must be a plain message id: letters, digits, `.`, `_` and `-`, starting with a letter or digit, at most 120 characters. Anything else is refused before sending, with an alert.

Signature: `sig = HMAC(key, "v1\n" + to + "\n" + reply_to + "\n" + ts + "\n" + nonce + "\n" + body)`. Split headers and body at the first `\n---\n`.

Test vector:

- to=`demo/claude-session`, reply_to=`01JTESTREPLYTO0000000000`, ts=`1700000000`, nonce=`0123456789abcdef01234567`
- body = `hello` + newline + `第二行`
- Expected sig=`652fbb03f1fd8626fd4f5db689b5983bdd57c797d5ba1085c340aaf699003088`

With a non-empty `reply_to`, `rn-cloud.sh send` derives the nonce instead of drawing it at random: `nonce = HMAC(key, "idem1\n" + to + "\n" + reply_to + "\n" + body)`, first 24 hex characters, where body is the same stripped body that is signed. Sending the same reply file again gives the same nonce with a new `ts`. A message without `reply_to` keeps a random nonce, because two identical messages that start contact may be intended.

Derived-nonce test vector:

- to=`demo/claude-session`, reply_to=`01JTESTREPLYTO0000000000`, body = `hello` + newline + `第二行`
- Expected nonce=`21dfab75a1eabc427d7655db`

The relay keeps a send record for every nonce it proxies, saved before it calls the engine and kept for `NONCE_KEEP_SECONDS` (default 86400, 24 hours). A repeated nonce that was already sent is dropped silently. A repeated nonce whose result is unknown (the engine timed out, answered `held` or gave no clear answer, or the relay stopped during the send) is not sent; the relay alerts once, and the Bot should check with the recipient, or change the text to send it anyway. A nonce the engine clearly refused, or one the relay refused before sending it (stale, hourly limit, group or own address, address not plain), may be sent again; an address that is not plain is refused again until it is corrected, so resending the same file does not help. Those refusals, failures and unknown results reach the routine as relay alerts. When a later try with the same nonce ends unknown, the relay withdraws that nonce's earlier refusal or failure alert, and the unknown alert starts by saying it replaces it.

Deduplication is a safety net; the Bot still should not resend a reply that is only queued. It works only when all of these hold: the relay and the cloud scripts are this version or later, the reply has a `reply_to`, the resend is byte-for-byte the same (the same reply file), and no more than `NONCE_KEEP_SECONDS` have passed since the first proxied send. Otherwise:

| Case | Result |
|---|---|
| The original arrives within 15 minutes and is sent; a resend arrives later | The resend is dropped silently |
| The resend arrives first and is sent; the original arrives later (fresh or stale) | The original is dropped silently |
| The original is held up for more than 15 minutes | The original is refused as stale (alert); nothing is recorded, so the resend is sent normally |
| The relay clearly refuses (engine refusal, hourly limit, group address) | Alert; a resend is handled normally |
| The relay cannot tell whether the send went out | One alert; a resend is not sent; change the text to send it |
| Two different replies to the same `reply_to` | Different nonces; both are sent |
| Messages that start contact (no `reply_to`) | Random nonces; both are sent |
| Resend after `NONCE_KEEP_SECONDS` | Sent as a new message |
| New cloud scripts, old relay | A resend within one hour is refused as a replay, with a notice (one extra wake, no duplicate); after one hour it is delivered twice |
| Old cloud scripts, new relay | Random nonces, as before |
| A nonce the relay recorded in the hour before its upgrade, resent after the upgrade | Treated as unknown: not sent, one alert; change the text to send it |
| The engine answers `held`, or the relay cannot classify its answer | Unknown: one alert; a resend with the same nonce is not sent |
| New routine prompt, old cloud scripts | The prompt does not ask for a resend after a timeout, so this is no worse than before |

### RN-FWD v1

The relay forwards a body to `bot-inbox`:

```text
RN-FWD v1
id: <message id>
from: <sender address>
agent: <sender agent type, e.g. claude-code>
thread: <thread id, or empty>
text_len: <body character count>
text_sha256: <SHA-256 of body>
ts: <Unix seconds>
sig: <hex>
caps: got1
---
<original body>
--- RN-FWD END
```

Signature: `sig = HMAC(key, "fwd1\n" + id + "\n" + from + "\n" + agent + "\n" + thread + "\n" + text_len + "\n" + text_sha256 + "\n" + ts)`. text_len counts Unicode code points (Python len), not bytes or grapheme clusters; SHA-256 uses UTF-8 bytes.

The `caps` line is optional and not signed. The relay adds `caps: got1` to say it accepts RN-GOT receipts; the cloud keeps it in the message's `meta.json` and sends receipts only for messages that carried it. Readers ignore header lines they do not know, so a copy from an older relay simply has no `caps` line.

Test vector:

- id=`01JTESTMSG0000000000000000`, from=`demo/claude-session`, agent=`claude-code`, empty thread, ts=`1700000000`
- text = `你好` + newline + ` x `
- Expected sig=`a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576`

### RN-REFWD v1

`bot-cloud` requests redelivery from `bot`; no body:

```text
RN-REFWD v1
id: <message id to resend>
ts: <Unix seconds>
nonce: <random hex>
sig: <hex>
```

Signature: `sig = HMAC(key, "refwd1\n" + id + "\n" + ts + "\n" + nonce)`. The first line identifies a re-forward request.

The cloud adds an optional, unsigned `caps: got1` line after `sig:` unless `RN_GOT=0`. With it, the relay only re-sends the copy, because the cloud's own fetch is already waiting for it. Without it (an older cloud or `RN_GOT=0`), the relay also queues a doorbell with `refwd`.

Test vector:

- id=`01JTESTMSG0000000000000000`, ts=`1700000000`, nonce=`0123456789abcdef01234567`
- Expected sig=`eb9e0a80d3126e9f8f026e584927e864871d390c39b5dec8cc02a7a6e2922668`

### RN-REFWD-ACK v1

Relay acknowledgement to `bot-inbox`:

```text
RN-REFWD-ACK v1
id: <message id>
status: <resent | no-copy | limit | failed>
attempt: <re-forward attempt number>
reason: <reason, or empty>
ts: <Unix seconds>
sig: <hex>
```

Signature: `sig = HMAC(key, "refwdack1\n" + id + "\n" + status + "\n" + attempt + "\n" + ts)`.

Test vector:

- Same id as above, status=`resent`, attempt=`1`, ts=`1700000000`
- Expected sig=`faa3cd29c693eb142c3f3bbb38fa0be8c46bcea4869f90ce9842aa41aa83f6ea`

### RN-GOT v1

Cloud receipt to `bot`, sent without `--from` (as a `<team-name>/terminal` session; older engines may number it, for example `terminal-3`, and newer receiving engines show it as `terminal@<CLOUD_MACHINE>`); the relay accepts it from `terminal`, `terminal-<n>`, `terminal@<CLOUD_MACHINE>` (only the configured cloud machine name), `bot-cloud` and `bot-inbox`:

```text
RN-GOT v1
stored: <comma-separated message ids, no spaces, may be empty>
done: <comma-separated message ids, no spaces, may be empty>
ts: <Unix seconds>
nonce: <random hex; 12 random bytes recommended>
sig: <hex>
```

Signature: `sig = HMAC(key, "got1\n" + stored + "\n" + done + "\n" + ts + "\n" + nonce)`; an empty list is an empty string. Each id matches `[A-Za-z0-9._-]{1,120}`. The two lists hold at most 100 ids together (more are split across messages), and at least one is non-empty; an id in both counts as done. Freshness (15 minutes) and replay protection are as for RN-REFWD. `stored` lists messages that `fetch` verified and stored, handed to a turn, or reported as `uncertain`; `done` lists handled ones. The cloud reports only ids whose RN-FWD carried `caps: got1`, plus relay alert batches (`relay-alerts-…`), which `rn-cloud.sh done` confirms.

Test vector:

- stored=`01JTESTMSG0000000000000000`, done=`01JTESTMSG0000000000000001`, ts=`1700000000`, nonce=`0123456789abcdef01234567`
- Expected sig=`26666d313c6c3dae158d5f8b3a6228f2be554c6ec58401662647f51aca361518`

### Doorbell

JSON POSTed to the routine (abbreviated values):

```text
{
  "source": "reason-network",
  "relay_version": 3,
  "doorbell": true,
  "to": "<team-name>/bot",
  "reply_via": "<team-name>/bot-cloud",
  "inbox": "<team-name>/bot-inbox",
  "messages": [
    {"id": …, "from": …, "agent": …, "thread": …, "text_len": …, "text_sha256": …, "preview": …}
  ]
}
```

An item may also contain `refwd` (re-forward attempt number), `rering` (the relay's own re-ring number for a message that stayed unconfirmed) and `forward_failed` (the reason the forward or re-sent copy failed). `refwd` doorbells appear only when the cloud's request did not declare `caps: got1`. Relay system notices use `doorbell:false` and carry `text` directly in each item. Relay alerts arrive as one notice item whose id starts with `relay-alerts-`; the routine confirms it with `rn-cloud.sh done <id>`, and the relay sends it again until then.
