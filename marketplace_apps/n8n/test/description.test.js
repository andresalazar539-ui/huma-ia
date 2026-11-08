// Checks the built package (dist/) without a running n8n instance.
// Run: npm run build && npm test
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const pkg = require(path.join(root, 'package.json'));
const { HumaIaOAuth2Api } = require(path.join(root, 'dist/credentials/HumaIaOAuth2Api.credentials.js'));
const { HumaIa } = require(path.join(root, 'dist/nodes/HumaIa/HumaIa.node.js'));
const { HumaIaTrigger } = require(path.join(root, 'dist/nodes/HumaIaTrigger/HumaIaTrigger.node.js'));

const EM_DASH = String.fromCharCode(0x2014);

function walk(dir) {
	return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			return ['node_modules', 'dist', '.git'].includes(entry.name) ? [] : walk(full);
		}
		return [full];
	});
}

test('package.json follows the n8n community node rules', () => {
	assert.equal(pkg.name, 'n8n-nodes-humaia');
	assert.ok(pkg.keywords.includes('n8n-community-node-package'));
	assert.equal(pkg.license, 'MIT');
	assert.deepEqual(pkg.files, ['dist']);
	assert.equal(pkg.dependencies, undefined, 'no runtime dependencies allowed');
	assert.ok(pkg.peerDependencies['n8n-workflow']);
	assert.equal(pkg.n8n.n8nNodesApiVersion, 1);
	assert.equal(pkg.n8n.strict, true);
	for (const file of [...pkg.n8n.credentials, ...pkg.n8n.nodes]) {
		assert.ok(fs.existsSync(path.join(root, file)), `${file} exists`);
	}
	assert.ok(fs.existsSync(path.join(root, '.github/workflows/publish.yml')));
});

test('no em dash in any source file', () => {
	for (const file of walk(root)) {
		if (!/\.(ts|js|json|md|yml|svg)$/.test(file)) continue;
		const text = fs.readFileSync(file, 'utf8');
		assert.ok(!text.includes(EM_DASH), `em dash found in ${path.relative(root, file)}`);
	}
});

test('credential is OAuth2 with PKCE and the fixed public client', () => {
	const cred = new HumaIaOAuth2Api();
	assert.equal(cred.name, 'humaIaOAuth2Api');
	assert.deepEqual(cred.extends, ['oAuth2Api']);
	const byName = Object.fromEntries(cred.properties.map((p) => [p.name, p]));
	const expected = {
		grantType: 'pkce',
		authUrl: 'https://app.humaia.com.br/oauth/mcp/authorize',
		accessTokenUrl: 'https://app.humaia.com.br/oauth/mcp/token',
		clientId: 'huma-n8n',
		clientSecret: '',
		scope: 'huma',
		authentication: 'body',
		useDynamicClientRegistration: false,
	};
	for (const [name, value] of Object.entries(expected)) {
		assert.ok(byName[name], `${name} is defined`);
		assert.equal(byName[name].type, 'hidden', `${name} is hidden`);
		assert.equal(byName[name].default, value, `${name} default`);
	}
	assert.equal(cred.test.request.baseURL, 'https://app.humaia.com.br/api/v1');
	assert.equal(cred.test.request.url, '/me');
});

test('action node has the conversation operations and no forbidden ones', () => {
	const node = new HumaIa();
	const d = node.description;
	assert.equal(d.name, 'humaIa');
	assert.equal(d.credentials[0].name, 'humaIaOAuth2Api');
	assert.equal(d.requestDefaults.baseURL, 'https://app.humaia.com.br/api/v1');
	const op = d.properties.find((p) => p.name === 'operation');
	const ops = Object.fromEntries(op.options.map((o) => [o.value, o]));
	assert.deepEqual(Object.keys(ops).sort(), ['addNote', 'get', 'handOff', 'moveToStage', 'search', 'sendMessage']);
	assert.equal(ops.search.routing.request.url, '/conversations');
	assert.equal(ops.sendMessage.routing.request.method, 'POST');
	assert.match(ops.sendMessage.routing.request.url, /encodeURIComponent\(\$parameter\.conversation\)\}\}\/messages$/);
	assert.match(ops.moveToStage.routing.request.url, /\/stage$/);
	assert.match(ops.addNote.routing.request.url, /\/notes$/);
	assert.match(ops.handOff.routing.request.url, /\/handoff$/);
	const conv = d.properties.find((p) => p.name === 'conversation');
	assert.equal(conv.type, 'resourceLocator');
	assert.equal(conv.modes[0].typeOptions.searchListMethod, 'getConversations');
	const stage = d.properties.find((p) => p.name === 'stage');
	assert.equal(stage.typeOptions.loadOptionsMethod, 'getStages');
	assert.equal(typeof node.methods.listSearch.getConversations, 'function');
	assert.equal(typeof node.methods.loadOptions.getStages, 'function');
	const text = JSON.stringify(d).toLowerCase();
	for (const word of ['payment', 'bulk', 'broadcast']) {
		assert.ok(!text.includes(word), `no "${word}" operation`);
	}
});

test('trigger node is programmatic with the six events', () => {
	const node = new HumaIaTrigger();
	const d = node.description;
	assert.equal(d.name, 'humaIaTrigger');
	assert.deepEqual(d.inputs, []);
	assert.deepEqual(d.webhooks, [{ name: 'default', httpMethod: 'POST', responseMode: 'onReceived', path: 'webhook' }]);
	const events = d.properties.find((p) => p.name === 'events').options.map((o) => o.value).sort();
	assert.deepEqual(events, [
		'appointment.confirmed',
		'contract.refused',
		'contract.signed',
		'lead.new',
		'lead.qualified',
		'payment.approved',
	]);
	assert.equal(typeof node.webhookMethods.default.checkExists, 'function');
	assert.equal(typeof node.webhookMethods.default.create, 'function');
	assert.equal(typeof node.webhookMethods.default.delete, 'function');
});

// Fake n8n context + fake HUMA IA hooks API, to exercise the trigger lifecycle.
const crypto = require('node:crypto');
const { computeSignature, isValidSignature } = require(path.join(root, 'dist/nodes/HumaIaTrigger/signature.js'));

function fakeContext({ url, events, staticData, server }) {
	return {
		getNodeWebhookUrl: () => url,
		getNodeParameter: (name, fallback) => (name === 'events' ? events : fallback),
		getWorkflowStaticData: () => staticData,
		getNode: () => ({ name: 'HUMA IA Trigger', type: 'n8n-nodes-humaia.humaIaTrigger', typeVersion: 1, parameters: {} }),
		helpers: {
			httpRequestWithAuthentication: async (credentialType, options) => {
				assert.equal(credentialType, 'humaIaOAuth2Api');
				return server.handle(options);
			},
		},
	};
}

// Mirrors the backend: secret only in the POST response, GET has target_url but no secret.
function fakeServer() {
	const hooks = new Map();
	let n = 0;
	const calls = [];
	const view = (h) => ({ id: h.id, event: h.event, target_url: h.url, target_host: new URL(h.url).hostname, active: true });
	return {
		hooks,
		calls,
		async handle(options) {
			const base = 'https://app.humaia.com.br/api/v1';
			assert.ok(options.url.startsWith(base));
			const p = options.url.slice(base.length);
			calls.push(`${options.method} ${p}`);
			if (options.method === 'GET' && p === '/hooks') {
				return [...hooks.values()].map(view);
			}
			if (options.method === 'POST' && p === '/hooks') {
				const { event, target_url: url, platform } = options.body;
				assert.equal(platform, 'n8n');
				const existing = [...hooks.values()].find((h) => h.event === event && h.url === url);
				if (existing) return { ...view(existing), secret: existing.secret };
				const id = `ahk_${++n}`;
				const hook = { id, event, url, secret: `sec_${n}` };
				hooks.set(id, hook);
				return { ...view(hook), secret: hook.secret };
			}
			if (options.method === 'DELETE' && p.startsWith('/hooks/')) {
				const id = decodeURIComponent(p.slice('/hooks/'.length));
				const existed = hooks.delete(id);
				return { id, deleted: true, existed };
			}
			throw new Error(`unexpected ${options.method} ${p}`);
		},
	};
}

const PROD_URL = 'https://n8n.example.com/webhook/abc/webhook';

test('trigger lifecycle: create stores secrets, check, change events, delete', async () => {
	const methods = new HumaIaTrigger().webhookMethods.default;
	const server = fakeServer();
	const staticData = {};
	let ctx = fakeContext({ url: PROD_URL, events: ['lead.new', 'lead.qualified'], staticData, server });

	assert.equal(await methods.checkExists.call(ctx), false);
	assert.equal(await methods.create.call(ctx), true);
	assert.equal(server.hooks.size, 2);
	const entry = staticData.hooksByUrl[PROD_URL];
	assert.deepEqual(Object.keys(entry.secrets).sort(), entry.ids.slice().sort());
	assert.ok(Object.values(entry.secrets).every((s) => s.startsWith('sec_')));
	assert.equal(await methods.checkExists.call(ctx), true);

	// A test URL living at the same time must not touch the production subscriptions.
	const testUrl = 'https://n8n.example.com/webhook-test/abc/webhook';
	const testCtx = fakeContext({ url: testUrl, events: ['lead.new'], staticData, server });
	assert.equal(await methods.create.call(testCtx), true);
	assert.equal(server.hooks.size, 3);
	assert.equal(await methods.delete.call(testCtx), true);
	assert.equal(server.hooks.size, 2);
	assert.equal(await methods.checkExists.call(ctx), true);

	// Hook disabled on the HUMA side (410 or failures): checkExists must see it.
	const [firstId] = server.hooks.keys();
	server.hooks.delete(firstId);
	assert.equal(await methods.checkExists.call(ctx), false);

	// User unselects an event: the old subscription is removed.
	ctx = fakeContext({ url: PROD_URL, events: ['lead.qualified'], staticData, server });
	assert.equal(await methods.create.call(ctx), true);
	assert.deepEqual([...server.hooks.values()].map((h) => h.event), ['lead.qualified']);
	assert.equal(await methods.checkExists.call(ctx), true);

	assert.equal(await methods.delete.call(ctx), true);
	assert.equal(server.hooks.size, 0);
	assert.equal(await methods.delete.call(ctx), true);
});

test('checkExists matches by URL when static data was lost, and recreates hooks without a secret', async () => {
	const methods = new HumaIaTrigger().webhookMethods.default;
	const server = fakeServer();
	const first = {};
	await methods.create.call(fakeContext({ url: PROD_URL, events: ['lead.new'], staticData: first, server }));
	const oldId = [...server.hooks.keys()][0];

	// Static data lost: the hook is found by event + URL, but has no secret here.
	const staticData = {};
	const ctx = fakeContext({ url: PROD_URL, events: ['lead.new'], staticData, server });
	server.calls.length = 0;
	assert.equal(await methods.checkExists.call(ctx), false);
	assert.ok(server.calls.includes(`DELETE /hooks/${oldId}`), 'old hook deleted');
	assert.equal(server.hooks.size, 0);
	assert.equal(await methods.create.call(ctx), true);
	const entry = staticData.hooksByUrl[PROD_URL];
	assert.equal(entry.ids.length, 1);
	assert.notEqual(entry.ids[0], oldId);
	assert.ok(entry.secrets[entry.ids[0]]);
	assert.equal(await methods.checkExists.call(ctx), true);

	// Legacy entry from version 0.1.0 (ids, no secrets) is treated the same way.
	const legacy = { hooksByUrl: { [PROD_URL]: { ids: entry.ids.slice(), events: ['lead.new'] } } };
	const legacyCtx = fakeContext({ url: PROD_URL, events: ['lead.new'], staticData: legacy, server });
	assert.equal(await methods.checkExists.call(legacyCtx), false);
	assert.equal(server.hooks.size, 0);
	assert.equal(await methods.create.call(legacyCtx), true);
	assert.ok(Object.keys(legacy.hooksByUrl[PROD_URL].secrets).length === 1);
});

test('create removes leftover hooks of the same URL for unselected events', async () => {
	const methods = new HumaIaTrigger().webhookMethods.default;
	const server = fakeServer();
	await methods.create.call(fakeContext({ url: PROD_URL, events: ['lead.new', 'payment.approved'], staticData: {}, server }));
	const staticData = {};
	await methods.create.call(fakeContext({ url: PROD_URL, events: ['lead.new'], staticData, server }));
	assert.deepEqual([...server.hooks.values()].map((h) => h.event), ['lead.new']);
	// The surviving hook was returned again by POST, with its secret.
	assert.ok(staticData.hooksByUrl[PROD_URL].secrets[staticData.hooksByUrl[PROD_URL].ids[0]]);
});

test('trigger refuses a non-https webhook URL with a clear message', async () => {
	const methods = new HumaIaTrigger().webhookMethods.default;
	const ctx = fakeContext({
		url: 'http://localhost:5678/webhook/abc/webhook',
		events: ['lead.new'],
		staticData: {},
		server: fakeServer(),
	});
	await assert.rejects(() => methods.create.call(ctx), /HTTPS/);
});

test('signature matches the HUMA IA backend reference vector', () => {
	// Produced by huma.services.lead_events.sign('s3cr3t', body, '1762600000').
	const body = Buffer.from('{"event": "lead.new", "nome": "João"}', 'utf8');
	const expected = 'sha256=67232e26c553a65ea94fcb7d73f68f2aee56415ac353013712d7122200a3da8e';
	assert.equal(computeSignature('s3cr3t', '1762600000', body), expected);
	assert.equal(isValidSignature('s3cr3t', '1762600000', body, expected), true);
	assert.equal(isValidSignature('s3cr3t', '1762600000', body, expected.toUpperCase()), true);
	assert.equal(isValidSignature('s3cr3t', '1762600001', body, expected), false);
	assert.equal(isValidSignature('other', '1762600000', body, expected), false);
	assert.equal(isValidSignature('s3cr3t', '1762600000', body, 'sha256=00'), false);
	assert.equal(isValidSignature('', '1762600000', body, expected), false);
	assert.equal(isValidSignature('s3cr3t', '', body, expected), false);
});

function webhookContext({ body, raw, headers, events = ['lead.qualified'], staticData = {} }) {
	const response = { statusCode: 200, payload: undefined };
	return {
		response,
		ctx: {
			getBodyData: () => body,
			getHeaderData: () => headers,
			getRequestObject: () => ({ rawBody: raw }),
			getResponseObject: () => ({
				status(code) {
					response.statusCode = code;
					return this;
				},
				json(data) {
					response.payload = data;
					return this;
				},
			}),
			getWorkflowStaticData: () => staticData,
			getNodeParameter: (name, fallback) => (name === 'events' ? events : fallback),
			helpers: { returnJsonArray: (data) => [{ json: data }] },
		},
	};
}

function signedHeaders(secret, raw, hookId, ts = '1762600000') {
	const sig = `sha256=${crypto.createHmac('sha256', secret).update(`${ts}.`).update(raw).digest('hex')}`;
	return { 'x-huma-hook-id': hookId, 'x-huma-timestamp': ts, 'x-huma-signature': sig, 'x-huma-event': 'lead.qualified' };
}

test('webhook verifies the signature over the raw body', async () => {
	const node = new HumaIaTrigger();
	const body = { id: 'evt_1', event: 'lead.qualified', lead: { id: '5511999998888', name: 'João' } };
	const raw = Buffer.from('{"id": "evt_1", "event": "lead.qualified", "lead": {"id": "5511999998888", "name": "João"}}', 'utf8');
	const staticData = { hooksByUrl: { [PROD_URL]: { ids: ['ahk_1'], events: ['lead.qualified'], secrets: { ahk_1: 'sec_1' } } } };

	// Valid signature: the workflow runs with the body.
	let w = webhookContext({ body, raw, headers: signedHeaders('sec_1', raw, 'ahk_1'), staticData });
	let out = await node.webhook.call(w.ctx);
	assert.deepEqual(out.workflowData, [[{ json: body }]]);

	// Wrong secret: 401 and no execution.
	w = webhookContext({ body, raw, headers: signedHeaders('wrong', raw, 'ahk_1'), staticData });
	out = await node.webhook.call(w.ctx);
	assert.deepEqual(out, { noWebhookResponse: true });
	assert.equal(w.response.statusCode, 401);

	// Body changed after signing: 401.
	const tampered = Buffer.from(raw.toString('utf8').replace('evt_1', 'evt_2'), 'utf8');
	w = webhookContext({ body, raw: tampered, headers: signedHeaders('sec_1', raw, 'ahk_1'), staticData });
	out = await node.webhook.call(w.ctx);
	assert.equal(w.response.statusCode, 401);

	// Unknown hook id while this node holds secrets: 401.
	w = webhookContext({ body, raw, headers: signedHeaders('sec_1', raw, 'ahk_999'), staticData });
	out = await node.webhook.call(w.ctx);
	assert.equal(w.response.statusCode, 401);

	// Missing signature header: 401.
	w = webhookContext({ body, raw, headers: { 'x-huma-hook-id': 'ahk_1' }, staticData });
	out = await node.webhook.call(w.ctx);
	assert.equal(w.response.statusCode, 401);
});

test('webhook accepts old hooks without a stored secret and ignores unselected events', async () => {
	const node = new HumaIaTrigger();
	const body = { id: 'evt_1', event: 'lead.qualified', lead: { id: '5511999998888' } };
	const raw = Buffer.from(JSON.stringify(body));
	const legacy = { hooksByUrl: { [PROD_URL]: { ids: ['ahk_1'], events: ['lead.qualified'] } } };
	let w = webhookContext({ body, raw, headers: { 'x-huma-hook-id': 'ahk_1' }, staticData: legacy });
	let out = await node.webhook.call(w.ctx);
	assert.deepEqual(out.workflowData, [[{ json: body }]]);

	w = webhookContext({ body: { ...body, event: 'lead.new' }, raw, headers: {}, staticData: {} });
	out = await node.webhook.call(w.ctx);
	assert.equal(out.workflowData, undefined);
});
