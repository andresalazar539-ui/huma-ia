import type {
	IDataObject,
	IExecuteFunctions,
	IHookFunctions,
	IHttpRequestMethods,
	IHttpRequestOptions,
	ILoadOptionsFunctions,
} from 'n8n-workflow';

export const HUMA_API_BASE_URL = 'https://app.humaia.com.br/api/v1';

export const HUMA_CREDENTIAL_TYPE = 'humaIaOAuth2Api';

/**
 * Calls the HUMA IA public API v1 with the OAuth2 credential.
 * n8n refreshes the access token automatically on 401.
 */
export async function humaApiRequest(
	this: IHookFunctions | IExecuteFunctions | ILoadOptionsFunctions,
	method: IHttpRequestMethods,
	resource: string,
	qs: IDataObject = {},
	body: IDataObject | undefined = undefined,
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
): Promise<any> {
	const options: IHttpRequestOptions = {
		method,
		qs,
		body,
		url: `${HUMA_API_BASE_URL}${resource}`,
		json: true,
		headers: {
			Accept: 'application/json',
		},
	};
	if (body === undefined) {
		delete options.body;
	}
	return this.helpers.httpRequestWithAuthentication.call(this, HUMA_CREDENTIAL_TYPE, options);
}
