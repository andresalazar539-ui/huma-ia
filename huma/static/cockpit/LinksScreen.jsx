// LinksScreen.jsx — Divulgação: link do Balcão + gerador de links rastreáveis
//
// Dois jobs do dono nesta tela:
//   1. Copiar o link do Balcão (chat do clone no navegador — bio do
//      Instagram, Google Business, e-mail) — canal de custo zero.
//   2. Gerar link wa.me rastreável por origem/campanha (código #h): a
//      primeira mensagem do lead identifica de onde ele veio e os
//      relatórios mostram origem → conversa → venda.
// Backend: GET /api/clients/{id}/tracking-link (attribution_service).
const { useState: useStateL } = React;

// Origens aceitas pelo backend (attribution_service). Meta Ads fica de
// fora do select: anúncio click-to-WhatsApp já chega com referral
// automático — gerar link manual pra ele só confundiria o dado.
const LINK_SOURCES = [
  { value: 'instagram', label: 'Instagram (bio, stories)' },
  { value: 'site', label: 'Site / botão do site' },
  { value: 'google_ads', label: 'Google Ads' },
  { value: 'youtube', label: 'YouTube' },
  { value: 'tiktok_ads', label: 'TikTok Ads' },
  { value: 'facebook', label: 'Facebook (página, grupos)' },
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'email', label: 'E-mail / assinatura' },
  { value: 'indicacao', label: 'Indicação' },
];

const linksCard = {
  background: 'var(--paper-raised)',
  border: '1px solid var(--paper-edge)',
  borderRadius: 12,
  padding: 20,
};
const linksLabel = {
  fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500,
  letterSpacing: '0.08em', textTransform: 'uppercase',
  color: 'var(--ink-3)', display: 'block', marginBottom: 6,
};
const linksInput = {
  width: '100%', boxSizing: 'border-box',
  border: '1px solid var(--paper-edge)', borderRadius: 8,
  background: 'var(--paper)', color: 'var(--ink)',
  fontFamily: 'var(--font-sans)', fontSize: 14,
  padding: '9px 12px', outline: 'none',
};

const CopyBtn = ({ text, small }) => {
  const [copied, setCopied] = useStateL(false);
  return (
    <button
      onClick={() => {
        // clipboard API só existe em contexto seguro (HTTPS) — em webview/
        // HTTP cai no prompt pro dono copiar manualmente (padrão UsageScreens).
        if (!navigator.clipboard) { window.prompt('Copie o link:', text); return; }
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1800);
        }).catch(() => { window.prompt('Copie o link:', text); });
      }}
      style={{
        border: 'none', cursor: 'pointer', flexShrink: 0,
        background: copied ? 'var(--sage-soft)' : 'var(--terracotta)',
        color: copied ? 'var(--sage-ink)' : 'var(--paper-raised)',
        fontFamily: 'var(--font-sans)', fontSize: small ? 12.5 : 13.5, fontWeight: 500,
        padding: small ? '6px 12px' : '9px 16px', borderRadius: 8,
        transition: 'background 160ms ease',
      }}
    >
      {copied ? 'Copiado!' : 'Copiar'}
    </button>
  );
};

const LinkBox = ({ url }) => (
  <div style={{
    display: 'flex', alignItems: 'center', gap: 10,
    background: 'var(--paper-sunk)', border: '1px solid var(--paper-edge)',
    borderRadius: 8, padding: '10px 12px',
  }}>
    <code style={{
      fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--ink-2)',
      flex: 1, overflowX: 'auto', whiteSpace: 'nowrap',
    }}>{url}</code>
    <CopyBtn text={url} small />
  </div>
);

const LinksScreen = () => {
  const balcaoUrl = window.getBalcaoUrl();

  // WhatsApp do negócio persiste no navegador — o dono digita uma vez.
  // Chave ESCOPADA por cliente: agência com dois cockpits no mesmo
  // navegador não pode herdar o número do cliente anterior.
  const phoneKey = `huma_links_phone_${window.getClientId()}`;
  const [bizPhone, setBizPhone] = useStateL(localStorage.getItem(phoneKey) || '');
  const [source, setSource] = useStateL('instagram');
  const [campaign, setCampaign] = useStateL('');
  const [result, setResult] = useStateL(null);
  const [busy, setBusy] = useStateL(false);
  const [error, setError] = useStateL('');

  const gerar = async () => {
    const phone = bizPhone.replace(/\D/g, '');
    if (phone.length < 12) {
      setError('Informe o WhatsApp do negócio com DDI e DDD (ex: 5511999998888).');
      return;
    }
    localStorage.setItem(phoneKey, phone);
    setBusy(true); setError(''); setResult(null);
    try {
      const data = await window.fetchTrackingLink(source, campaign.trim(), phone);
      setResult(data);
    } catch (e) {
      // 401/403 (sessão expirada) e 400 (origem inválida) têm mensagens
      // úteis do backend — repassar em vez de mandar tentar de novo à toa.
      setError(String(e.message || 'Não consegui gerar o link agora. Tente de novo.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px 32px', boxSizing: 'border-box' }}>
      <div style={{ maxWidth: 720, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <h1 style={{
            fontFamily: 'var(--font-serif)', fontWeight: 400,
            fontSize: 30, margin: 0, color: 'var(--ink)',
          }}>Divulgação</h1>
          <p style={{ margin: '6px 0 0', fontSize: 14, color: 'var(--ink-3)', maxWidth: '58ch' }}>
            Os links que trazem leads pra sua HUMA — e mostram de onde cada venda veio.
          </p>
        </div>

        {/* ── Link do Balcão ── */}
        <div style={linksCard}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <Icon name="link" size={16} />
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0, color: 'var(--ink)' }}>
              Chat do seu negócio no navegador
            </h2>
          </div>
          <p style={{ margin: '0 0 14px', fontSize: 13.5, color: 'var(--ink-3)', maxWidth: '60ch', lineHeight: 1.5 }}>
            Sua HUMA atendendo numa página própria, sem precisar de site nem de WhatsApp.
            Cole na bio do Instagram, no Google e onde mais o cliente te procura.
            Quando o lead esquentar, ela pede o WhatsApp dele e te avisa.
          </p>
          <LinkBox url={balcaoUrl} />
        </div>

        {/* ── Gerador de link rastreável ── */}
        <div style={linksCard}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <Icon name="send" size={16} />
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0, color: 'var(--ink)' }}>
              Link de WhatsApp com origem
            </h2>
          </div>
          <p style={{ margin: '0 0 16px', fontSize: 13.5, color: 'var(--ink-3)', maxWidth: '60ch', lineHeight: 1.5 }}>
            Cada canal ganha um link próprio. Quando o lead manda a primeira mensagem,
            a HUMA reconhece de onde ele veio — e o relatório mostra qual canal vende.
            Anúncio do Instagram/Facebook (click-to-WhatsApp) não precisa: a origem já chega sozinha.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div>
              <label style={linksLabel} htmlFor="lk-src">Onde vai colar</label>
              <select id="lk-src" value={source} onChange={e => setSource(e.target.value)} style={linksInput}>
                {LINK_SOURCES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label style={linksLabel} htmlFor="lk-camp">Campanha (opcional)</label>
              <input id="lk-camp" value={campaign} maxLength={40}
                onChange={e => setCampaign(e.target.value)}
                placeholder="ex: promo-agosto" style={linksInput} />
            </div>
          </div>
          <div style={{ marginBottom: 16 }}>
            <label style={linksLabel} htmlFor="lk-phone">WhatsApp do negócio (com DDI)</label>
            <input id="lk-phone" value={bizPhone} inputMode="numeric" maxLength={20}
              onChange={e => setBizPhone(e.target.value.replace(/[^\d+\s().-]/g, ''))}
              placeholder="5511999998888" style={linksInput} />
          </div>

          <button onClick={gerar} disabled={busy} style={{
            border: 'none', cursor: busy ? 'wait' : 'pointer',
            background: 'var(--ink)', color: 'var(--paper)',
            fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
            padding: '10px 20px', borderRadius: 8, opacity: busy ? 0.6 : 1,
          }}>
            {busy ? 'Gerando…' : 'Gerar link'}
          </button>

          {error && (
            <p style={{ margin: '12px 0 0', fontSize: 13, color: 'var(--danger)' }}>{error}</p>
          )}

          {result && result.link && (
            <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span style={linksLabel}>Seu link rastreável</span>
              <LinkBox url={result.link} />
              <p style={{ margin: 0, fontSize: 12.5, color: 'var(--ink-4)' }}>
                O texto da primeira mensagem já vem preenchido com o código {result.code ? `(${result.code})` : 'de origem'} —
                é ele que identifica o canal. Não edite o final da mensagem.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { LinksScreen });
