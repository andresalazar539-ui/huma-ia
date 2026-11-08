import {
	NodeApiError,
	NodeConnectionTypes,
	NodeOperationError,
	type IDataObject,
	type IHookFunctions,
	type INodeType,
	type INodeTypeDescription,
	type IWebhookFunctions,
	type IWebhookResponseData,
	type JsonObject,
} from 'n8n-workflow';
import { humaApiRequest, HUMA_CREDENTIAL_TYPE } from '../HumaIa/shared/transport';
import { isValidSignature } from './signature';

type HookRow = {
	id?: string;
	event?: string;
	target_url?: string;
	active?: boolean;
	secret?: string;
};

type HookEntry = {
	ids: string[];
	events: string[];
	/** Signing secret per hook id. HUMA IA returns it only when the hook is created. */
	secrets?: Record<string, string>;
};

/**
 * Subscriptions are stored per webhook URL, because n8n uses one URL for
 * "Listen for test event" and another one for the active workflow, and both
 * can exist at the same time.
 */
type HookStaticData = {
	hooksByUrl?: Record<string, HookEntry>;
};

type RawBodyRequest = {
	rawBody?: Buffer;
	readRawBody?: () => Promise<void>;
};

function selectedEvents(context: IHookFunctions | IWebhookFunctions): string[] {
	const events = context.getNodeParameter('events', []) as string[];
	return Array.from(new Set(events));
}

function isNotFound(error: unknown): boolean {
	const err = error as { httpCode?: string; response?: { status?: number }; statusCode?: number };
	return (
		err?.httpCode === '404' || err?.response?.status === 404 || err?.statusCode === 404
	);
}

function headerValue(headers: IDataObject, name: string): string {
	const value = headers[name.toLowerCase()];
	if (Array.isArray(value)) return String(value[0] ?? '');
	return typeof value === 'string' ? value : '';
}

async function listActiveHooks(this: IHookFunctions): Promise<HookRow[]> {
	const hooks = (await humaApiRequest.call(this, 'GET', '/hooks')) as HookRow[];
	return (Array.isArray(hooks) ? hooks : []).filter((hook) => hook.id && hook.active !== false);
}

async function deleteHook(this: IHookFunctions, id: string): Promise<void> {
	try {
		await humaApiRequest.call(this, 'DELETE', `/hooks/${encodeURIComponent(id)}`);
	} catch (error) {
		if (!isNotFound(error)) {
			throw new NodeApiError(this.getNode(), error as JsonObject, {
				message: 'Could not turn off a HUMA IA trigger subscription',
			});
		}
	}
}

export class HumaIaTrigger implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'HUMA IA Trigger',
		name: 'humaIaTrigger',
		icon: { light: 'file:../../icons/humaia.svg', dark: 'file:../../icons/humaia.dark.svg' },
		group: ['trigger'],
		version: 1,
		subtitle: '={{$parameter["events"].join(", ")}}',
		description: 'Starts the workflow when something happens in HUMA IA',
		defaults: {
			name: 'HUMA IA Trigger',
		},
		inputs: [],
		outputs: [NodeConnectionTypes.Main],
		credentials: [
			{
				name: HUMA_CREDENTIAL_TYPE,
				required: true,
			},
		],
		webhooks: [
			{
				name: 'default',
				httpMethod: 'POST',
				responseMode: 'onReceived',
				path: 'webhook',
			},
		],
		properties: [
			{
				displayName:
					'Only the owner or an admin of the HUMA IA account can turn on triggers. Your n8n instance must be reachable over HTTPS.',
				name: 'notice',
				type: 'notice',
				default: '',
			},
			{
				displayName: 'Events',
				name: 'events',
				type: 'multiOptions',
				required: true,
				default: [],
				description: 'The events that start the workflow',
				options: [
					{
						name: 'Appointment Confirmed',
						value: 'appointment.confirmed',
						description: 'HUMA IA booked an appointment in the calendar',
					},
					{
						name: 'Contract Refused',
						value: 'contract.refused',
						description: 'The lead refused the contract sent by HUMA IA',
					},
					{
						name: 'Contract Signed',
						value: 'contract.signed',
						description: 'The lead signed the contract sent by HUMA IA',
					},
					{
						name: 'Lead Qualified',
						value: 'lead.qualified',
						description: 'HUMA IA qualified the lead and handed it to the team',
					},
					{
						name: 'New Lead',
						value: 'lead.new',
						description: 'A lead talked to HUMA IA for the first time',
					},
					{
						name: 'Payment Approved',
						value: 'payment.approved',
						description: 'A payment made in the conversation was approved',
					},
				],
			},
		],
	};

	webhookMethods = {
		default: {
			async checkExists(this: IHookFunctions): Promise<boolean> {
				const webhookUrl = this.getNodeWebhookUrl('default') as string;
				const staticData = this.getWorkflowStaticData('node') as HookStaticData;
				const entry = staticData.hooksByUrl?.[webhookUrl];
				const storedIds = new Set(entry?.ids ?? []);
				const secrets = entry?.secrets ?? {};
				const events = selectedEvents(this);
				if (events.length === 0) return false;

				const hooks = await listActiveHooks.call(this);

				// Match each selected event by the stored hook id, or by event + this
				// webhook URL (covers static data that was lost or never saved).
				const matched: HookRow[] = [];
				for (const event of events) {
					const hook =
						hooks.find((h) => storedIds.has(h.id as string) && h.event === event) ??
						hooks.find((h) => h.event === event && h.target_url === webhookUrl);
					if (!hook) return false;
					matched.push(hook);
				}

				// A hook without a stored secret cannot be verified: delete it so that
				// create() makes a new one and receives its secret.
				const withoutSecret = matched.filter((hook) => !secrets[hook.id as string]);
				if (withoutSecret.length > 0) {
					for (const hook of withoutSecret) {
						await deleteHook.call(this, hook.id as string);
					}
					return false;
				}

				const hooksByUrl = staticData.hooksByUrl ?? {};
				hooksByUrl[webhookUrl] = {
					ids: matched.map((hook) => hook.id as string),
					events,
					secrets,
				};
				staticData.hooksByUrl = hooksByUrl;
				return true;
			},

			async create(this: IHookFunctions): Promise<boolean> {
				const webhookUrl = this.getNodeWebhookUrl('default') as string;
				if (!webhookUrl || !webhookUrl.toLowerCase().startsWith('https://')) {
					throw new NodeOperationError(
						this.getNode(),
						'HUMA IA can only send events to an HTTPS address',
						{
							description: `The webhook URL of this n8n instance is "${webhookUrl}". HUMA IA delivers events over the internet and only to public HTTPS addresses. Set WEBHOOK_URL on your n8n instance to a public https:// address (or use n8n Cloud or a tunnel) and activate the workflow again.`,
						},
					);
				}

				const events = selectedEvents(this);
				if (events.length === 0) {
					throw new NodeOperationError(this.getNode(), 'Select at least one event');
				}

				const staticData = this.getWorkflowStaticData('node') as HookStaticData;
				const hooksByUrl = staticData.hooksByUrl ?? {};
				const previous = hooksByUrl[webhookUrl];
				const newIds: string[] = [];
				const secrets: Record<string, string> = {};

				for (const event of events) {
					let hook: HookRow;
					try {
						hook = (await humaApiRequest.call(this, 'POST', '/hooks', {}, {
							event,
							target_url: webhookUrl,
							platform: 'n8n',
						})) as HookRow;
					} catch (error) {
						throw new NodeApiError(this.getNode(), error as JsonObject, {
							message: `Could not turn on the "${event}" trigger in HUMA IA`,
						});
					}
					if (!hook?.id) {
						throw new NodeOperationError(
							this.getNode(),
							'HUMA IA did not return a subscription ID',
						);
					}
					newIds.push(hook.id);
					const secret = hook.secret || previous?.secrets?.[hook.id];
					if (secret) secrets[hook.id] = secret;
				}

				// Remove subscriptions of this URL that are no longer needed (an event
				// that was unselected, or leftovers from lost static data).
				const stale = new Set((previous?.ids ?? []).filter((id) => !newIds.includes(id)));
				for (const hook of await listActiveHooks.call(this)) {
					if (hook.target_url === webhookUrl && !newIds.includes(hook.id as string)) {
						stale.add(hook.id as string);
					}
				}
				for (const id of stale) {
					await deleteHook.call(this, id);
				}

				hooksByUrl[webhookUrl] = { ids: newIds, events, secrets };
				staticData.hooksByUrl = hooksByUrl;
				return true;
			},

			async delete(this: IHookFunctions): Promise<boolean> {
				const webhookUrl = this.getNodeWebhookUrl('default') as string;
				const staticData = this.getWorkflowStaticData('node') as HookStaticData;
				const ids = new Set(staticData.hooksByUrl?.[webhookUrl]?.ids ?? []);
				for (const hook of await listActiveHooks.call(this)) {
					if (hook.target_url === webhookUrl) ids.add(hook.id as string);
				}
				for (const id of ids) {
					await deleteHook.call(this, id);
				}
				if (staticData.hooksByUrl) {
					delete staticData.hooksByUrl[webhookUrl];
				}
				return true;
			},
		},
	};

	async webhook(this: IWebhookFunctions): Promise<IWebhookResponseData> {
		const body = this.getBodyData() as IDataObject;
		const headers = this.getHeaderData() as IDataObject;
		const hookId = headerValue(headers, 'x-huma-hook-id');

		const staticData = this.getWorkflowStaticData('node') as HookStaticData;
		const entries = Object.values(staticData.hooksByUrl ?? {});
		const secret = entries.map((entry) => entry.secrets?.[hookId]).find(Boolean) ?? '';
		const anySecretStored = entries.some(
			(entry) => Object.keys(entry.secrets ?? {}).length > 0,
		);

		// Hooks created before HUMA IA signed deliveries have no secret: accept them.
		// Once this node holds a secret, every delivery must be signed by a known hook.
		if (secret || anySecretStored) {
			const req = this.getRequestObject() as unknown as RawBodyRequest;
			if (!req.rawBody && typeof req.readRawBody === 'function') {
				await req.readRawBody();
			}
			// n8n keeps the raw request body in req.rawBody. Re-serializing the parsed
			// body is only a last resort and may not match byte for byte.
			const rawBody: Buffer | string = req.rawBody ?? JSON.stringify(body);
			const valid = isValidSignature(
				secret,
				headerValue(headers, 'x-huma-timestamp'),
				rawBody,
				headerValue(headers, 'x-huma-signature'),
			);
			if (!valid) {
				const res = this.getResponseObject();
				res.status(401).json({ error: 'invalid signature' });
				return { noWebhookResponse: true };
			}
		}

		const events = selectedEvents(this);
		const event = typeof body?.event === 'string' ? body.event : '';

		// Ignore deliveries for events that are not selected on this node.
		if (events.length > 0 && event && !events.includes(event)) {
			return {
				webhookResponse: { received: true, ignored: true },
			};
		}

		return {
			workflowData: [this.helpers.returnJsonArray(body)],
		};
	}
}
