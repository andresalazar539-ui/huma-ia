# HUMA IA public API v1 (automation platforms)

Contract shared by the Zapier, Make and n8n apps. Source of truth: `huma/routes/automation.py`,
`huma/services/automation_hooks.py`, `huma/core/mcp_auth.py`, `huma/routes/mcp.py`.

Production base URL: `https://app.humaia.com.br`. API base: `https://app.humaia.com.br/api/v1`.

## Authentication: OAuth 2.0 authorization code + PKCE (S256 mandatory)

| | |
|---|---|
| Authorize URL | `https://app.humaia.com.br/oauth/mcp/authorize` |
| Token URL | `https://app.humaia.com.br/oauth/mcp/token` (form-urlencoded or JSON) |
| Scope | `huma` (optional) |
| client_id | Zapier: `huma-zapier` · Make: `huma-make` · n8n: `huma-n8n` |
| client_secret | not required, not checked (public client + PKCE). Send any placeholder if the platform demands a value. |
| Redirect URIs accepted | Zapier: `https://zapier.com/dashboard/auth/oauth/return/<anything>/` · Make: `https://www.make.com/oauth/cb/app` (or legacy `https://www.integromat.com/oauth/cb/app`) · n8n: any `https://<host>/.../oauth2-credential/callback`, plus `http://localhost` / `http://127.0.0.1` with that path for local dev |

Authorize query: `response_type=code&client_id=…&redirect_uri=…&state=…&code_challenge=…&code_challenge_method=S256`.
The user logs in to HUMA (if needed) and clicks "Conectar". Redirect back carries `code`, `state`, `iss`.

Token exchange (`grant_type=authorization_code`): `code`, `client_id`, `redirect_uri`, `code_verifier`.
Refresh (`grant_type=refresh_token`): `refresh_token` and `client_id` (REQUIRED: the long refresh token is rejected without the platform client_id).

Response: `{"access_token", "token_type": "Bearer", "expires_in": 3600, "refresh_token", "scope": "huma"}`.
- Access token lasts 1 hour. On 401 the platform must refresh.
- The refresh token for these three client_ids lasts 365 days and can be used more than once (not single-use). Each refresh returns a new refresh token; store it.
- Errors: HTTP 400 `{"error": "invalid_grant", "error_description": "..."}`. `invalid_grant` on refresh = the owner disconnected in HUMA; ask to reconnect.

All API calls: `Authorization: Bearer <access_token>`, JSON. Errors: `{"detail": "<message in Portuguese>"}` with
400 (bad input), 422 (missing/invalid field, `detail` is a readable string), 401 (token invalid/expired, refresh), 403 (role not allowed), 404, 429 (rate limit 240/min), 503.

## Endpoints

### `GET /api/v1/me` (connection test + label)
`{"client_id": "cli_x", "business_name": "Clínica Sorriso", "email": "dono@x.com", "role": "dono", "label": "Clínica Sorriso (dono@x.com)"}`

### `GET /api/v1/events` (dropdown)
`[{"id": "lead.new", "label": "...", "help": "..."}, ...]`. Events: `lead.new`, `lead.qualified`,
`appointment.confirmed`, `payment.approved`, `contract.signed`, `contract.refused`.

### `GET /api/v1/events/{event}/sample`
Array (1 to 3 items) of recent REAL events of the account in exactly the delivery shape, or one example (`"exemplo": true`) when the account has none.

### Hooks (REST hooks, for instant triggers). Only owner/admin roles (others get 403).
- `POST /api/v1/hooks` body `{"event": "lead.qualified", "target_url": "https://hooks.zapier.com/...", "platform": "zapier|make|n8n"}` → **201** `{"id": "ahk_...", "event", "platform", "target_url", "target_host", "created_by", "created_at", "last_delivery_at", "last_status", "active", "secret"}`. `secret` is returned ONLY here (and when the same event+URL is re-created): store it to verify deliveries. Same event + same URL returns the existing hook (idempotent). target_url must be https and public.
- `DELETE /api/v1/hooks/{id}` → 200 `{"id", "deleted": true, "existed": bool}` (idempotent; deleting a missing hook is still 200).
- `GET /api/v1/hooks` → array of hooks (with `target_url`, never `secret`).
- `POST /api/v1/hooks/{id}/test` → sends ONE example event (`"exemplo": true, "teste": true`) to that hook now; returns `{"id", "event", "http", "delivered"}`. 404 if the hook is not in this account. Use it to fire payment/contract triggers in review scenarios.

Delivery: HUMA POSTs JSON to target_url, headers `X-HUMA-Event`, `X-HUMA-Hook-Id`, `X-HUMA-Timestamp`, `X-HUMA-Signature: sha256=<hex HMAC-SHA256(secret, timestamp + "." + raw body)>`, `User-Agent: HUMA-IA-Automacao/1.0`.
2xx = delivered. **410 Gone = HUMA disables the hook.** 20 consecutive failures also disable it. No retries.

Payload (same as sample):
```json
{
  "id": "evt_Ab12Cd34Ef56Gh78",
  "event": "lead.qualified",
  "event_label": "Lead qualificado",
  "at": "2026-11-08T12:00:00+00:00",
  "client_id": "cli_x",
  "business_name": "Clínica Sorriso",
  "lead": {
    "name": "Maria Souza", "phone": "5511999998888", "id": "5511999998888",
    "email": "maria@exemplo.com.br", "channel": "whatsapp", "source": "instagram",
    "source_detail": "", "stage": "qualified",
    "facts": ["Quer clareamento dental", "Prefere sábado de manhã"],
    "assigned_to": "", "assigned_name": ""
  },
  "data": {
    "summary": "...", "service": "", "when": "", "value_cents": 0, "value_brl": 0,
    "method": "", "payment_id": ""
  },
  "conversation_url": "https://app.humaia.com.br/cockpit?screen=conversas"
}
```
`lead.id` is the conversation id used by the actions below (phone digits for WhatsApp; `ig:<id>` for Instagram; `web:<id>` for the website chat). `lead.phone` is empty for Instagram/web. `data` may have extra keys per event (e.g. `document_id`, `provider` on contract events).

### `GET /api/v1/stages` (dropdown)
`[{"id": "discovery", "label": "Descobrindo"}, ...]`: discovery, offer, closing, committed, qualified, won, lost.

### `GET /api/v1/conversations?q=&limit=25&offset=0` (dropdown + "Find conversation" search)
Paginated by `offset` over the 200 most recent conversations (limit max 100). Array of `{"id", "phone", "lead_name", "stage", "handoff_status", "channel", "last_message_at", "last_message_preview", "assigned_name", "assigned_to", "lead_facts"}`. `q` matches name or phone digits. Empty array when nothing matches. Limit max 100.

### `GET /api/v1/conversations/{id}`
Same fields + `lead_email`, `owner_notes`, `is_customer`, `lead_source`, `active_appointment_datetime`, `active_appointment_service`, and `messages` (last 20: `{"from", "at", "text"}`).

### Actions (existing conversation only; there is NO action to start a conversation with a new contact)
- `POST /api/v1/conversations/{id}/messages` `{"text": "..."}` (1 to 1600 chars) → `{"id", "sent": true, "result": {...}}`. Sent on the lead's channel; the HUMA AI keeps answering.
- `POST /api/v1/conversations/{id}/stage` `{"stage": "won"}` → `{"id", "stage", "result"}`.
- `POST /api/v1/conversations/{id}/notes` `{"text": "...", "replace": false}` → `{"id", "notes"}` (appends by default; HUMA's AI reads the notes).
- `POST /api/v1/conversations/{id}/handoff` `{"takeover": true}` → `{"id", "handoff_status": "handed_off"|"active", "result"}`. true = a human takes over and HUMA stays silent; false = give it back to HUMA.

## Rules for every app
- User-facing UI text in English (marketplace requirement); data stays in Portuguese.
- Never ask the user to type an ID: use the conversations dropdown/search.
- No payment action, no bulk send, no "send to a new number".
- Brand name: "HUMA IA". Homepage: https://humaia.com.br. Support: contato@humaia.com.br. Docs: https://app.humaia.com.br/automacoes
