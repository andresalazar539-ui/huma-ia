// onboarding.jsx — shell: máquina de momentos 1→7 + tela final, 401 global
const { useState, useEffect } = React;
function OnboardingApp() {
  const [moment, setMoment] = useState(1);
  const [authLost, setAuthLost] = useState(false);
  const [facts, setFacts] = useState({ name: '', products: 0, faqs: 0, answers: 0 });
  useEffect(() => {
    const h = () => setAuthLost(true);
    window.addEventListener('huma-auth', h);
    // retomar de onde parou: se já está em sandbox/active, pula pro ponto certo
    HumaAPI.state().then(st => {
      setFacts(f => ({ ...f, name: st.business_name || '' }));
      if (st.onboarding_status === 'active') setMoment(8);
      else if (st.onboarding_status === 'sandbox') setMoment(5);
    }).catch(() => {});
    return () => window.removeEventListener('huma-auth', h);
  }, []);
  const next = () => setMoment(m => m + 1);
  const fromProposal = (p) => {
    setFacts(f => ({
      ...f,
      name: (p && p.business_name) || f.name,
      products: (p && p.products_or_services && p.products_or_services.length) || 0,
      faqs: (p && p.faq && p.faq.length) || 0,
    }));
    setMoment(3);
  };
  return <div className="ob-app">
    {moment >= 2 && moment <= 7 && <div className="ob-top"><DotBar step={moment} /></div>}
    {moment === 1 && <Moment1 onNext={next} key="m1" />}
    {moment === 2 && <Moment2 onDone={fromProposal} onSkip={() => setMoment(3)} key="m2" />}
    {moment === 3 && <Moment3 onDone={(answers) => { setFacts(f => ({ ...f, answers: answers || f.answers })); setMoment(4); }} key="m3" />}
    {moment === 4 && <Moment4 onDone={next} key="m4" />}
    {moment === 5 && <Moment5 businessName={facts.name} onDone={next} key="m5" />}
    {moment === 6 && <Moment6 onDone={next} key="m6" />}
    {moment === 7 && <Moment7 onFinish={next} key="m7" />}
    {moment === 8 && <FinalScreen summary={facts} key="fim" />}
    {authLost && <div className="authveil" role="alertdialog" aria-label="Sessão expirada">
      <div className="card">
        <HumaAvatar />
        <h3 style={{ fontFamily: 'var(--font-serif)', fontWeight: 400, fontSize: 24 }}>Sua sessão deu uma cochilada.</h3>
        <p className="ob-micro" style={{ fontSize: 14.5 }}>Entra de novo que eu continuo exatamente de onde a gente parou.</p>
        <a href="/login" style={{ textDecoration: 'none' }}><ObButton>Entrar de novo</ObButton></a>
      </div>
    </div>}
  </div>;
}
ReactDOM.createRoot(document.getElementById('root')).render(<OnboardingApp />);
