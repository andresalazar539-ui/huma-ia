# Zapier publishing form: ready-to-paste texts

Copy each block into the matching field of the Zapier Developer Platform (Settings and Publishing).

## Integration name

HUMA IA

## Short description (140 characters max, 114 used)

HUMA IA is an AI sales assistant that answers, qualifies and closes leads on WhatsApp, Instagram and website chat.

## Long description

HUMA IA is an AI sales assistant for small and medium businesses in Brazil. It answers every lead on WhatsApp, Instagram Direct and the website chat in the owner's own tone of voice, asks the right questions, books appointments in Google Calendar, sends payment links and hands qualified leads to the right salesperson. Owners follow every conversation, the sales funnel and the results in the HUMA IA Cockpit.

With this integration you can send HUMA IA events (new lead, qualified lead, confirmed appointment, approved payment, signed or refused contract) to your CRM, spreadsheets, email or team chat the moment they happen, and let other apps act on a conversation: send a message to the lead, move the lead to another funnel stage, add an internal note that the AI reads, or hand the conversation to a human and back.

## Homepage URL

https://humaia.com.br

## Category

Primary: AI Sales Tools

Secondary (if the form allows more): AI Chatbots, Sales & CRM, Customer Support

## Intended audience

Public (global). Customers of HUMA IA: business owners and admins of clinics, beauty studios, online stores, real estate agencies and service businesses in Brazil.

## Role

Employee of the company that owns HUMA IA.

## Support

- Support email: contato@humaia.com.br
- Help and documentation: https://app.humaia.com.br/automacoes

## Authentication notes for reviewers

OAuth 2.0 authorization code with PKCE (S256). Click "Connect", sign in to HUMA IA with the test account below and click "Conectar". Access tokens last 1 hour and refresh automatically; refresh tokens last 1 year. Only owner and admin roles can turn on triggers.

## Test account for the Zapier review team

- Email: integration-testing@zapier.com
- Access: courtesy account (no charge), owner role, with demo leads
- Sign in at: https://app.humaia.com.br/login

## Popular use cases (for the form and the app profile)

1. Add every new HUMA IA lead to Google Sheets, HubSpot or Pipedrive.
2. Notify the sales team in Slack or by email when HUMA IA qualifies a lead.
3. Create a record in your clinic or service software when HUMA IA confirms an appointment.
4. Log approved payments in your accounting or invoicing tool.
5. When a deal is marked won in your CRM, move the HUMA IA lead to Won and add a note.

## Trigger and action list

Triggers: New Lead, Lead Qualified, Appointment Confirmed, Payment Approved, Contract Signed, Contract Refused.

Actions: Send Message, Move Lead to Stage, Add Note to Lead, Hand Off Conversation.

Search: Find Conversation.

## Notes for reviewers

- All triggers are instant (REST hooks). The editor shows real recent events from the connected account, or one example event when the account has none yet.
- Send Message only works on an existing conversation. The integration has no bulk send, no payment action and no way to message a new phone number, by design (WhatsApp anti-spam rules).
- Data such as lead names, summaries and stage names stays in Portuguese, the language of our customers.
