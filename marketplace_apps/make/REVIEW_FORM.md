# HUMA IA: texts for the Make app review

Ready-to-paste answers for the **Review** tab and for the follow-up form Make sends by email.
Replace only the `<...>` links with the real scenario links after running the test scenarios (README, step 6).

> Reminder: publishing is irreversible. Published apps cannot be made private or deleted, and published components cannot be deleted. Publish only after every test scenario ran successfully.

## Review tab

**API documentation link**

```
https://app.humaia.com.br/automacoes
```

**Test scenarios**

```
1. Qualified lead, full flow (Watch Qualified Leads, Get a Conversation, Add a Note, Move Lead to Stage, Send a Message, Hand Off Conversation x2): <link>
2. New lead (Watch New Leads, Add a Note): <link>
3. Confirmed appointment (Watch Confirmed Appointments, Get a Conversation): <link>
4. Approved payment (Watch Approved Payments, Add a Note; fired with the HUMA IA test delivery endpoint POST /api/v1/hooks/{id}/test): <link>
5. Signed contract (Watch Signed Contracts, Add a Note; fired with the test delivery endpoint): <link>
6. Refused contract (Watch Refused Contracts, Add a Note; fired with the test delivery endpoint): <link>
7. Searches and universal module (Make an API Call, Router, Search Conversations at the end of each route; route B uses Limit 25 with a page size of 10, so the log shows 3 pages): <link>
8. Error scenario (Send a Message to a conversation that does not exist; the API returns HTTP 400 with its message): <link>
```

**Notes for the reviewer** (paste if there is a free-text field, or reply to the reviewer email)

```
HUMA IA is a Brazilian AI sales assistant that answers leads on WhatsApp, Instagram and website chat, qualifies them, books appointments and closes sales. The app is built and maintained by HUMA IA, the vendor of the API.

Authentication: OAuth 2.0 authorization code with PKCE (S256), public client "huma-make" (no client secret; the secret field holds a placeholder that the server ignores). Redirect URI: https://www.make.com/oauth/cb/app (oauth.localRedirectUri). Access tokens last 1 hour and are refreshed 15 minutes before expiry; refresh tokens last 365 days and rotate. The connection is validated with GET /api/v1/me, which also provides the connection label.

Triggers: six instant triggers, each linked to its own dedicated attached webhook. Attach calls POST /api/v1/hooks {event, target_url, platform: "make"} and stores the returned id; detach calls DELETE /api/v1/hooks/{id} (idempotent). The webhook only accepts deliveries whose body.event matches its event. Only the owner or an admin of the HUMA IA account can register hooks (the API returns 403 otherwise).

Actions work only on conversations that already exist: the app has no module to message a new contact, no bulk send and no payment module, by product policy. Conversation and stage inputs use RPC dropdowns (ListConversations, ListStages) and can also be mapped from the trigger output.

Search Conversations and the ListConversations RPC paginate with offset (GET /api/v1/conversations?limit=&offset=) over the 200 most recent conversations. The module requests 10 per page, advances offset by (pagination.page - 1) * 10, stops on a short page, and honors the user Limit (1 to 200) through response.limit.

Webhook security: every HUMA IA delivery is signed (X-HUMA-Timestamp and X-HUMA-Signature, HMAC-SHA256 with a per-hook secret returned when the hook is created). The app stores that secret in the webhook data (sanitized in logs). Make webhooks expose the parsed body rather than the raw signed bytes, so the signature is not recomputed inside Make; the dedicated webhook URL remains the shared secret.

Error handling: errors come from the "detail" field of the API response (messages are in Brazilian Portuguese, the language of the product), with specific hints for 401 and 403. No generic error type is set, so 429 and 5xx keep Make defaults. Authorization headers, codes, verifiers and tokens are sanitized in logs.

Test data: all scenarios use a test lead created through the website chat (no phone number) and fictitious example payloads (marked "exemplo" and "teste"). No personal data appears in the logs.

Support: contato@humaia.com.br. Website: https://humaia.com.br. API documentation: https://app.humaia.com.br/automacoes
```

## Follow-up form (sent by email after "Request review")

| Field | Answer |
|---|---|
| Developer information (relationship to the API vendor) | We are the vendor. HUMA IA develops and operates the HUMA IA platform and its API. |
| Partnership contact (ISV) | André Salazar, founder. contato@humaia.com.br |
| Support contact | HUMA IA support, contato@humaia.com.br (Portuguese and English) |
| Categories | Primary: Marketing > Chatbots (or the closest "AI" / "Communication" category offered). Secondary: CRM. |
| Company logo (ISV) | `assets/icon-512.png` from this folder (512x512 PNG) |
| Service URL | https://humaia.com.br |
| Trademark compliance | (The owner confirms.) HUMA IA owns the "HUMA IA" name and logo used in the app. |
| External service terms compliance | (The owner confirms.) The app only calls the HUMA IA API, operated by us, and follows its own terms. |

**App description for the listing** (if asked)

```
HUMA IA is an AI sales assistant that answers your leads on WhatsApp, Instagram and your website 24/7, qualifies them, books appointments and closes sales. With the HUMA IA app for Make you can send new and qualified leads, confirmed appointments, approved payments and signed contracts to any tool, and act on conversations: send a message, move the lead through the funnel, add notes for the AI and hand the conversation to your team.
```

**Short description** (if asked, 1 line)

```
AI sales assistant for WhatsApp, Instagram and website chat: react to leads, appointments and payments, and act on conversations.
```
