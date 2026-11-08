import type { Icon, ICredentialTestRequest, ICredentialType, INodeProperties } from 'n8n-workflow';

/**
 * HUMA IA uses OAuth 2.0 authorization code with PKCE (S256) and a public client.
 * The client ID is fixed (`huma-n8n`) and there is no client secret, so the user
 * only clicks "Connect my account", logs in to HUMA IA and approves.
 */
export class HumaIaOAuth2Api implements ICredentialType {
	name = 'humaIaOAuth2Api';

	extends = ['oAuth2Api'];

	displayName = 'HUMA IA OAuth2 API';

	icon: Icon = { light: 'file:../icons/humaia.svg', dark: 'file:../icons/humaia.dark.svg' };

	documentationUrl = 'https://github.com/andresalazar539-ui/n8n-nodes-humaia?tab=readme-ov-file#credentials';

	properties: INodeProperties[] = [
		{
			displayName: 'Use Dynamic Client Registration',
			name: 'useDynamicClientRegistration',
			type: 'hidden',
			default: false,
		},
		{
			displayName: 'Grant Type',
			name: 'grantType',
			type: 'hidden',
			default: 'pkce',
		},
		{
			displayName: 'Authorization URL',
			name: 'authUrl',
			type: 'hidden',
			default: 'https://app.humaia.com.br/oauth/mcp/authorize',
			required: true,
		},
		{
			displayName: 'Access Token URL',
			name: 'accessTokenUrl',
			type: 'hidden',
			default: 'https://app.humaia.com.br/oauth/mcp/token',
			required: true,
		},
		{
			displayName: 'Client ID',
			name: 'clientId',
			type: 'hidden',
			default: 'huma-n8n',
		},
		{
			displayName: 'Client Secret',
			name: 'clientSecret',
			type: 'hidden',
			typeOptions: { password: true },
			default: '',
		},
		{
			displayName: 'Scope',
			name: 'scope',
			type: 'hidden',
			default: 'huma',
		},
		{
			displayName: 'Auth URI Query Parameters',
			name: 'authQueryParameters',
			type: 'hidden',
			default: '',
		},
		{
			displayName: 'Authentication',
			name: 'authentication',
			type: 'hidden',
			default: 'body',
		},
	];

	test: ICredentialTestRequest = {
		request: {
			baseURL: 'https://app.humaia.com.br/api/v1',
			url: '/me',
		},
	};
}
