import type { INodeProperties } from 'n8n-workflow';
import { conversationSelect } from '../../shared/descriptions';

const CONVERSATION_PATH = '=/conversations/{{encodeURIComponent($parameter.conversation)}}';

const showForConversation = {
	resource: ['conversation'],
};

export const conversationDescription: INodeProperties[] = [
	{
		displayName: 'Operation',
		name: 'operation',
		type: 'options',
		noDataExpression: true,
		displayOptions: {
			show: showForConversation,
		},
		options: [
			{
				name: 'Add Note',
				value: 'addNote',
				action: 'Add a note to a conversation',
				description:
					'Add an internal note about the lead. The HUMA IA assistant reads the notes when it answers.',
				routing: {
					request: {
						method: 'POST',
						url: `${CONVERSATION_PATH}/notes`,
					},
				},
			},
			{
				name: 'Get',
				value: 'get',
				action: 'Get a conversation',
				description: 'Get a conversation with lead details, notes and the last 20 messages',
				routing: {
					request: {
						method: 'GET',
						url: CONVERSATION_PATH,
					},
				},
			},
			{
				name: 'Hand Off',
				value: 'handOff',
				action: 'Hand off a conversation',
				description:
					'Let a person take over the conversation (HUMA IA stays silent) or give it back to HUMA IA',
				routing: {
					request: {
						method: 'POST',
						url: `${CONVERSATION_PATH}/handoff`,
					},
				},
			},
			{
				name: 'Move to Stage',
				value: 'moveToStage',
				action: 'Move a conversation to a stage',
				description: 'Move the lead to another stage of the sales funnel',
				routing: {
					request: {
						method: 'POST',
						url: `${CONVERSATION_PATH}/stage`,
					},
				},
			},
			{
				name: 'Search',
				value: 'search',
				action: 'Search conversations',
				description: 'Search recent conversations by lead name or phone number',
				routing: {
					request: {
						method: 'GET',
						url: '/conversations',
					},
				},
			},
			{
				name: 'Send Message',
				value: 'sendMessage',
				action: 'Send a message in a conversation',
				description:
					'Send a text message to the lead on their channel (WhatsApp, Instagram or website chat). HUMA IA keeps answering.',
				routing: {
					request: {
						method: 'POST',
						url: `${CONVERSATION_PATH}/messages`,
					},
				},
			},
		],
		default: 'get',
	},
	{
		...conversationSelect,
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['addNote', 'get', 'handOff', 'moveToStage', 'sendMessage'],
			},
		},
	},
	{
		displayName: 'Search Term',
		name: 'searchTerm',
		type: 'string',
		default: '',
		placeholder: 'e.g. Maria or 11999998888',
		description: 'Lead name or phone digits. Leave empty to get the most recent conversations.',
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['search'],
			},
		},
		routing: {
			send: {
				type: 'query',
				property: 'q',
			},
		},
	},
	{
		displayName: 'Limit',
		name: 'limit',
		type: 'number',
		default: 50,
		typeOptions: {
			minValue: 1,
			maxValue: 100,
		},
		description: 'Max number of results to return',
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['search'],
			},
		},
		routing: {
			send: {
				type: 'query',
				property: 'limit',
			},
		},
	},
	{
		displayName: 'Text',
		name: 'text',
		type: 'string',
		required: true,
		default: '',
		typeOptions: {
			rows: 4,
		},
		description: 'Message text, up to 1600 characters',
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['sendMessage'],
			},
		},
		routing: {
			send: {
				type: 'body',
				property: 'text',
			},
		},
	},
	{
		displayName: 'Stage Name or ID',
		name: 'stage',
		type: 'options',
		required: true,
		default: '',
		typeOptions: {
			loadOptionsMethod: 'getStages',
		},
		description:
			'Funnel stage to move the lead to. Choose from the list, or specify an ID using an <a href="https://docs.n8n.io/code/expressions/">expression</a>.',
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['moveToStage'],
			},
		},
		routing: {
			send: {
				type: 'body',
				property: 'stage',
			},
		},
	},
	{
		displayName: 'Note',
		name: 'note',
		type: 'string',
		required: true,
		default: '',
		typeOptions: {
			rows: 4,
		},
		description: 'Note text, up to 2000 characters',
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['addNote'],
			},
		},
		routing: {
			send: {
				type: 'body',
				property: 'text',
			},
		},
	},
	{
		displayName: 'Replace Existing Notes',
		name: 'replace',
		type: 'boolean',
		default: false,
		description:
			'Whether to replace all existing notes with this one. By default the note is added after the existing notes.',
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['addNote'],
			},
		},
		routing: {
			send: {
				type: 'body',
				property: 'replace',
			},
		},
	},
	{
		displayName: 'Mode',
		name: 'handOffMode',
		type: 'options',
		default: 'takeOver',
		options: [
			{
				name: 'Give Back to HUMA IA',
				value: 'giveBack',
				description: 'HUMA IA starts answering the lead again',
			},
			{
				name: 'Take Over',
				value: 'takeOver',
				description: 'A person answers the lead and HUMA IA stays silent',
			},
		],
		displayOptions: {
			show: {
				...showForConversation,
				operation: ['handOff'],
			},
		},
		routing: {
			send: {
				type: 'body',
				property: 'takeover',
				value: '={{ $value === "takeOver" }}',
			},
		},
	},
];
