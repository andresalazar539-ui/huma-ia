// PlaceholderScreens.jsx — telas simples para os menus ainda não detalhados
const PlaceholderScreen = ({ title, eyebrow, subtitle, children }) => (
  <div style={{
    flex: 1, overflow: 'auto', background: 'var(--paper)',
    display: 'flex', flexDirection: 'column',
  }}>
    <div style={{
      padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
    }}>
      <Eyebrow>{eyebrow}</Eyebrow>
      <div style={{
        fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
        letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4,
      }}>{title}</div>
      {subtitle && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
          {subtitle}
        </div>
      )}
    </div>
    <div style={{ padding: '24px 32px 40px', maxWidth: 1280, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {children}
    </div>
  </div>
);

const ClientesScreen = () => (
  <PlaceholderScreen eyebrow="clientes" title="Base de clientes" subtitle="847 clientes · 12 novos esta semana">
    <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', overflow: 'hidden' }}>
      {[
        { name: 'Beatriz Campos',    phone: '+55 11 9****-3847', last: 'Limpeza de pele · hoje 14h',        tone: 'terracotta', tag: 'Confirmada hoje' },
        { name: 'Camila Ribeiro',    phone: '+55 11 9****-1122', last: 'Botox · remarcando',                 tone: 'sage',       tag: 'Aguarda' },
        { name: 'Fernanda Alves',    phone: '+55 11 9****-0455', last: 'Consulta · hoje 16h',                tone: 'ink',        tag: 'Confirmada hoje' },
        { name: 'Isabela Moreira',   phone: '+55 11 9****-6623', last: 'Microagulhamento · hoje 17h30',      tone: 'terracotta', tag: 'Confirmada hoje' },
        { name: 'Rita Cavalcanti',   phone: '+55 11 9****-9901', last: 'Botox testa · hoje 10h30',           tone: 'sage',       tag: 'Feito' },
        { name: 'Ana Paula Souza',   phone: '+55 11 9****-2288', last: 'Limpeza · hoje 09h',                 tone: 'ink',        tag: 'Feito' },
        { name: 'Juliana Torres',    phone: '+55 11 9****-7733', last: 'Avaliação · hoje 11h15',             tone: 'terracotta', tag: 'Feito' },
      ].map((c, i) => (
        <div key={i} style={{
          display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
          borderTop: i ? '1px solid var(--paper-edge)' : 'none',
        }}>
          <Avatar initials={c.name.split(' ').map(n => n[0]).slice(0,2).join('')} tone={c.tone} size={34}/>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)' }}>{c.name}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{c.phone}</div>
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', flex: 1.4 }}>
            {c.last}
          </div>
          <span style={{
            fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
            padding: '3px 9px', borderRadius: 999,
            background: 'var(--paper-sunk)', color: 'var(--ink-3)',
          }}>{c.tag}</span>
        </div>
      ))}
    </div>
  </PlaceholderScreen>
);

// AgendaFullScreen agora vive em AgendaScreen.jsx (4 views: dia/semana/mes/lista).
// Removido daqui pra evitar redeclaracao de const no escopo global do Babel.

const VozScreen = () => (
  <PlaceholderScreen eyebrow="voz" title="Voz clonada" subtitle="Dra. Marina · v_mR4nA_2024_a7f3">
    <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{
          width: 56, height: 56, borderRadius: 999,
          background: 'var(--terracotta-tint)', color: 'var(--terracotta)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon name="mic" size={26}/>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18, color: 'var(--ink)', letterSpacing: '-0.015em' }}>
            Sua voz, treinada
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 2 }}>
            32 áudios enviados esta semana · última amostra há 3 dias
          </div>
        </div>
        <Button variant="primary" size="sm" icon={<Icon name="play" size={13}/>}>Ouvir amostra</Button>
      </div>
      <div style={{ marginTop: 20, padding: 16, background: 'var(--paper-sunk)', borderRadius: 12 }}>
        <VoiceClipInline duration="18"/>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', marginTop: 12, fontStyle: 'italic' }}>
          "Oi, Beatriz. Seu horário de limpeza de pele está confirmado para quarta, 14h. Te lembro na véspera."
        </div>
      </div>
    </div>
  </PlaceholderScreen>
);

const AjustesScreen = () => (
  <PlaceholderScreen eyebrow="ajustes" title="Ajustes do estúdio" subtitle="Conta · plano · preferências">
    {[
      { title: 'Conta', desc: 'Estúdio Marina · Jardins, SP' },
      { title: 'Plano', desc: 'HUMA Pro · R$ 390/mês · próxima cobrança 05 mai' },
      { title: 'Horário de atendimento', desc: 'HUMA responde 24h · lembretes entre 08h e 21h' },
      { title: 'Equipe', desc: 'Marina Costa (dona) · Sofia Ramos (recepção)' },
      { title: 'Notificações', desc: 'WhatsApp para mensagens críticas · e-mail diário às 08h' },
    ].map((s, i) => (
      <div key={i} style={{
        display: 'flex', alignItems: 'center', gap: 14,
        border: '1px solid var(--paper-edge)', borderRadius: 14,
        background: 'var(--paper-raised)', padding: 18,
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)' }}>{s.title}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 2 }}>{s.desc}</div>
        </div>
        <Button variant="ghost" size="sm">Editar</Button>
      </div>
    ))}
  </PlaceholderScreen>
);

Object.assign(window, { ClientesScreen, VozScreen, AjustesScreen });
