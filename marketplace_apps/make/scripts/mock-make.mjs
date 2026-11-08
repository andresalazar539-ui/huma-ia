// In-memory stand-in for the parts of @makehq/sdk@1.8.1 that upload.mjs uses.
// Lets `node scripts/upload.mjs --mock` exercise the whole upload sequence offline. It proves the script
// logic only; it is NOT evidence that Make accepts the sections.

export class Make {
  constructor() {
    const apps = new Map(); // name -> { version, public, sections, docs, icon }
    const connections = new Map(); // name -> { label, type, app, sections, common }
    const webhooks = new Map(); // name -> { label, type, app, connection, sections }
    const rpcs = new Map(); // `${app}/${version}/${name}` -> { name, label, connection, sections }
    const modules = new Map(); // `${app}/${version}/${name}` -> { ...meta, sections }
    let seq = 0;
    const need = (map, key) => {
      if (!map.has(key)) { const e = new Error('not found'); e.statusCode = 404; throw e; }
      return map.get(key);
    };
    this.users = {
      currentAuthorization: async () => ({ authUsed: 'token', scope: ['sdk-apps:read', 'sdk-apps:write'] }),
    };
    this.sdk = {
      apps: {
        list: async () => [...apps.entries()].map(([name, a]) => ({ name, version: a.version, public: a.public, label: a.label })),
        create: async (body) => { apps.set(body.name, { version: 1, public: body.private === true ? false : true, label: body.label, sections: {} }); return { name: body.name, version: 1, public: false }; },
        update: async (name, version, body) => ({ name, version, ...body }),
        get: async (name, version) => { const a = need(apps, name); return { name, version: a.version, public: a.public }; },
        setSection: async (name, v, section, body) => { need(apps, name).sections[section] = body; },
        getSection: async (name, v, section) => JSON.parse(need(apps, name).sections[section]),
        setDocs: async (name, v, docs) => { need(apps, name).docs = docs; return true; },
        getDocs: async (name) => need(apps, name).docs,
        setIcon: async (name, v, data) => {
          const b = data instanceof Uint8Array ? data : new Uint8Array(data);
          const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
          if (view.getUint32(16) !== 512 || view.getUint32(20) !== 512) throw new Error('icon must be 512x512');
          need(apps, name).icon = b; return true;
        },
      },
      connections: {
        list: async (app) => [...connections.entries()].filter(([, c]) => c.app === app).map(([name, c]) => ({ name, label: c.label, type: c.type })),
        create: async (app, body) => { const name = seq++ ? `${app}${seq}` : app; connections.set(name, { ...body, app, sections: {}, common: {} }); return { name, label: body.label, type: body.type }; },
        setSection: async (name, section, body) => { need(connections, name).sections[section] = body; },
        getSection: async (name, section) => need(connections, name).sections[section],
        setCommon: async (name, body) => { need(connections, name).common = body; return true; },
        getCommon: async (name) => need(connections, name).common,
      },
      webhooks: {
        list: async (app) => [...webhooks.entries()].filter(([, w]) => w.app === app).map(([name, w]) => ({ name, label: w.label, type: w.type })),
        create: async (app, body) => { const name = `${app}${++seq}`; webhooks.set(name, { ...body, app, sections: {} }); return { label: body.label, type: body.type, name }; },
        update: async (name, body) => Object.assign(need(webhooks, name), body),
        get: async (name) => { const w = need(webhooks, name); return { name, label: w.label, type: w.type, connection: w.connection ?? null }; },
        setSection: async (name, section, body) => { need(webhooks, name).sections[section] = body; },
        getSection: async (name, section) => need(webhooks, name).sections[section],
      },
      rpcs: {
        list: async (app, v) => [...rpcs.values()].filter((r) => r.key.startsWith(`${app}/${v}/`)).map((r) => ({ name: r.name, label: r.label })),
        create: async (app, v, body) => { rpcs.set(`${app}/${v}/${body.name}`, { ...body, key: `${app}/${v}/${body.name}`, sections: {} }); return { name: body.name, label: body.label }; },
        update: async (app, v, name, body) => Object.assign(need(rpcs, `${app}/${v}/${name}`), body),
        get: async (app, v, name) => { const r = need(rpcs, `${app}/${v}/${name}`); return { name, label: r.label, connection: r.connection ?? null, altConnection: null }; },
        setSection: async (app, v, name, section, body) => { need(rpcs, `${app}/${v}/${name}`).sections[section] = body; },
        getSection: async (app, v, name, section) => need(rpcs, `${app}/${v}/${name}`).sections[section],
      },
      modules: {
        list: async (app, v) => [...modules.values()].filter((m) => m.key.startsWith(`${app}/${v}/`)).map((m) => ({ name: m.name, label: m.label, typeId: m.typeId })),
        create: async (app, v, body) => { modules.set(`${app}/${v}/${body.name}`, { ...body, key: `${app}/${v}/${body.name}`, public: false, sections: {} }); return { name: body.name, typeId: body.typeId }; },
        update: async (app, v, name, body) => Object.assign(need(modules, `${app}/${v}/${name}`), body),
        get: async (app, v, name) => { const m = need(modules, `${app}/${v}/${name}`); return { name, typeId: m.typeId, label: m.label, connection: m.connection ?? null, webhook: m.webhook ?? null, public: false }; },
        setSection: async (app, v, name, section, body) => { need(modules, `${app}/${v}/${name}`).sections[section] = body; },
        getSection: async (app, v, name, section) => need(modules, `${app}/${v}/${name}`).sections[section],
      },
    };
  }
}
