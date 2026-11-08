import { NodeConnectionTypes, type INodeType, type INodeTypeDescription } from 'n8n-workflow';
import { conversationDescription } from './resources/conversation';
import { getConversations } from './listSearch/getConversations';
import { getStages } from './loadOptions/getStages';
import { HUMA_API_BASE_URL, HUMA_CREDENTIAL_TYPE } from './shared/transport';

export class HumaIa implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'HUMA IA',
		name: 'humaIa',
		icon: { light: 'file:../../icons/humaia.svg', dark: 'file:../../icons/humaia.dark.svg' },
		group: ['output'],
		version: 1,
		subtitle: '={{$parameter["operation"] + ": " + $parameter["resource"]}}',
		description:
			'Work with HUMA IA, the AI sales assistant for WhatsApp, Instagram and website chat',
		defaults: {
			name: 'HUMA IA',
		},
		usableAsTool: true,
		inputs: [NodeConnectionTypes.Main],
		outputs: [NodeConnectionTypes.Main],
		credentials: [
			{
				name: HUMA_CREDENTIAL_TYPE,
				required: true,
			},
		],
		requestDefaults: {
			baseURL: HUMA_API_BASE_URL,
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
			},
		},
		properties: [
			{
				displayName: 'Resource',
				name: 'resource',
				type: 'options',
				noDataExpression: true,
				options: [
					{
						name: 'Conversation',
						value: 'conversation',
					},
				],
				default: 'conversation',
			},
			...conversationDescription,
		],
	};

	methods = {
		listSearch: {
			getConversations,
		},
		loadOptions: {
			getStages,
		},
	};
}
