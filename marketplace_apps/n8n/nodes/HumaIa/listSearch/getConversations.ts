import type {
	ILoadOptionsFunctions,
	INodeListSearchItems,
	INodeListSearchResult,
} from 'n8n-workflow';
import { humaApiRequest } from '../shared/transport';

type ConversationItem = {
	id?: string;
	phone?: string;
	lead_name?: string;
	channel?: string;
	stage?: string;
};

const CHANNEL_LABELS: Record<string, string> = {
	whatsapp: 'WhatsApp',
	instagram: 'Instagram',
	web: 'Website chat',
};

export async function getConversations(
	this: ILoadOptionsFunctions,
	filter?: string,
): Promise<INodeListSearchResult> {
	const response = (await humaApiRequest.call(this, 'GET', '/conversations', {
		q: filter ?? '',
		limit: 100,
	})) as ConversationItem[];

	const results: INodeListSearchItems[] = (Array.isArray(response) ? response : [])
		.filter((item) => item.id)
		.map((item) => {
			const who = item.lead_name || item.phone || (item.id as string);
			const channel = CHANNEL_LABELS[item.channel ?? ''] ?? item.channel ?? '';
			return {
				name: channel ? `${who} (${channel})` : who,
				value: item.id as string,
				url: 'https://app.humaia.com.br/cockpit?screen=conversas',
			};
		});

	return { results };
}
