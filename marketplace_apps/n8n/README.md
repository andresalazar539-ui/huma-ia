# n8n-nodes-humaia

This is an n8n community node package for [HUMA IA](https://humaia.com.br), the AI sales assistant that answers leads on WhatsApp, Instagram Direct and website chat for small businesses.

With it you can start n8n workflows when something happens in HUMA IA (new lead, qualified lead, appointment, payment, contract) and act on conversations from n8n (send a message, move the lead in the sales funnel, add a note, hand the conversation to a person).

[n8n](https://n8n.io/) is a [fair-code licensed](https://docs.n8n.io/sustainable-use-license/) workflow automation platform.

- [Installation](#installation)
- [Credentials](#credentials)
- [Nodes and operations](#nodes-and-operations)
- [Event payload](#event-payload)
- [Compatibility](#compatibility)
- [Usage notes](#usage-notes)
- [Resources](#resources)

## Installation

Follow the [installation guide](https://docs.n8n.io/integrations/community-nodes/installation/) in the n8n community nodes documentation.

Self-hosted n8n: go to **Settings > Community Nodes > Install**, enter `n8n-nodes-humaia` and confirm.

## Credentials

The package uses **HUMA IA OAuth2 API** (OAuth 2.0 with PKCE). There is nothing to copy or paste:

1. You need a HUMA IA account. The person who connects must be the **owner** or an **admin** of the account to use the trigger.
2. In n8n, create a new credential of type **HUMA IA OAuth2 API**.
3. Click **Connect my account**, log in to HUMA IA if asked, and click **Conectar** on the HUMA IA screen.
4. The credential shows as connected. n8n renews the access automatically.

To revoke access, open the HUMA IA Cockpit, go to **Integrations** and disconnect n8n in the AI assistants and automations section. After that the credential stops working and you need to connect again.

## Nodes and operations

### HUMA IA Trigger

Starts a workflow when one of the selected events happens in your HUMA IA account. When the workflow is activated, the node subscribes to the events in HUMA IA; when it is deactivated, the subscription is removed.

| Event | When it fires |
|---|---|
| New Lead | A lead talked to HUMA IA for the first time |
| Lead Qualified | HUMA IA qualified the lead and handed it to the team |
| Appointment Confirmed | HUMA IA booked an appointment in the calendar |
| Payment Approved | A payment made in the conversation was approved |
| Contract Signed | The lead signed the contract sent by HUMA IA |
| Contract Refused | The lead refused the contract sent by HUMA IA |

### HUMA IA

Resource **Conversation**:

| Operation | What it does |
|---|---|
| Add Note | Adds an internal note to the lead (or replaces all notes). The HUMA IA assistant reads the notes when it answers. |
| Get | Gets a conversation with lead details, stage, notes and the last 20 messages |
| Hand Off | **Take Over**: a person answers and HUMA IA stays silent. **Give Back to HUMA IA**: HUMA IA answers again. |
| Move to Stage | Moves the lead to a funnel stage: Discovery, Offer, Closing, Committed, Qualified, Won or Lost |
| Search | Searches recent conversations by lead name or phone number (up to 100 results) |
| Send Message | Sends a text message (up to 1600 characters) to the lead on their own channel. HUMA IA keeps answering. |

Select the conversation from the list (search by name or phone) or pass the `lead.id` field from a trigger event with an expression, for example `{{ $json.lead.id }}`.

The node can also be used as a tool by the n8n AI Agent.

By design there is no operation to start a conversation with a new number, to send bulk messages or to create payments.

## Event payload

Every event has the same shape. Lead data is in Portuguese, as written in HUMA IA.

```json
{
  "id": "evt_Ab12Cd34Ef56Gh78",
  "event": "lead.qualified",
  "event_label": "Lead qualificado",
  "at": "2026-11-08T12:00:00+00:00",
  "client_id": "cli_x",
  "business_name": "Clinica Sorriso",
  "lead": {
    "name": "Maria Souza",
    "phone": "5511999998888",
    "id": "5511999998888",
    "email": "maria@exemplo.com.br",
    "channel": "whatsapp",
    "source": "instagram",
    "source_detail": "",
    "stage": "qualified",
    "facts": ["Quer clareamento dental", "Prefere sabado de manha"],
    "assigned_to": "",
    "assigned_name": ""
  },
  "data": {
    "summary": "...",
    "service": "",
    "when": "",
    "value_cents": 0,
    "value_brl": 0,
    "method": "",
    "payment_id": ""
  },
  "conversation_url": "https://app.humaia.com.br/cockpit?screen=conversas"
}
```

- `id` is unique per delivery. Use it to avoid processing the same event twice.
- `lead.id` is the conversation ID used by the HUMA IA node. For Instagram it starts with `ig:` and for website chat with `web:`; in those cases `lead.phone` is empty.
- `data` can have extra keys depending on the event (for example `document_id` and `provider` on contract events).

## Compatibility

- Requires n8n 1.x or newer with community nodes enabled.
- Built with `@n8n/node-cli` 0.55 and tested on Node.js 24.

## Usage notes

- **HTTPS is required for the trigger.** HUMA IA only delivers events to public `https://` addresses. On n8n Cloud this works out of the box. On self-hosted n8n set the `WEBHOOK_URL` environment variable of your n8n instance to its public HTTPS address. A local `http://localhost` n8n cannot receive events (the action node still works).
- To test the trigger, click **Listen for test event** in the node and then cause the event in HUMA IA (for example, send a message from another phone to the business WhatsApp to create a new lead).
- **Every delivery is verified.** When the trigger subscribes, HUMA IA returns a signing secret that the node keeps in the workflow static data. Each delivery carries `X-HUMA-Timestamp` and `X-HUMA-Signature: sha256=<HMAC-SHA256(secret, timestamp + "." + raw body)>`, and the node checks it in constant time over the raw request body. A delivery with a missing or wrong signature gets HTTP 401 and does not start the workflow. Subscriptions created before signing existed have no secret: they are accepted, and the node replaces them with signed ones the next time the workflow is activated.
- HUMA IA does not retry a failed delivery. After 20 failed deliveries in a row the subscription is disabled; deactivate and activate the workflow to subscribe again.
- API rate limit: 240 requests per minute per connection.
- Error messages from the HUMA IA API are in Portuguese.

## Resources

- [n8n community nodes documentation](https://docs.n8n.io/integrations/#community-nodes)
- [HUMA IA](https://humaia.com.br)
- [HUMA IA automations help](https://app.humaia.com.br/automacoes)
- Support: contato@humaia.com.br

## License

[MIT](LICENSE.md)
