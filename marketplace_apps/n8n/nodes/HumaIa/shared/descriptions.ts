import type { INodeProperties } from 'n8n-workflow';

export const conversationSelect: INodeProperties = {
	displayName: 'Conversation',
	name: 'conversation',
	type: 'resourceLocator',
	default: {
		mode: 'list',
		value: '',
	},
	required: true,
	description: 'The conversation with the lead. Search by lead name or phone number.',
	modes: [
		{
			displayName: 'From List',
			name: 'list',
			type: 'list',
			placeholder: 'Select a conversation...',
			typeOptions: {
				searchListMethod: 'getConversations',
				searchable: true,
			},
		},
		{
			displayName: 'By ID',
			name: 'id',
			type: 'string',
			placeholder: 'e.g. 5511999998888',
			hint: 'Use the "lead.id" field from a HUMA IA Trigger event',
		},
	],
};
