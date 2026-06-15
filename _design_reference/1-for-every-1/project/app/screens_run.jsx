/* Forge — Playground (chat + graph live run) */
function PlaygroundScreen() {
  const [mode, setMode] = useState('chat');
  const [msgs, setMsgs] = useState([
    { who: 'u', text: 'I was charged twice for order #8842 — can you refund the duplicate?' },
  ]);
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState('');
  const [runStep, setRunStep] = useState(-1);
  const [input, setInput] = useState('');
  const timer = useRef(null);
  const scrollRef = useRef(null);
  const order = DATA.runOrder;

  const answer = "I can see order #8842 was charged twice on June 5 — $110.00 each. I've pulled the order and confirmed one is a duplicate authorization. Since this refund is over $100, I've routed it for quick human approval. You'll see $110.00 back on your card in 5–7 business days.";

  const send = () => {
    const text = input.trim() || 'Yes, please refund the duplicate.';
    setInput('');
    setMsgs(m => [...m, { who: 'u', text }]);
    setStreaming(true); setStreamText(''); setRunStep(0);
    let s = 0;
    const stepInt = setInterval(() => { s++; setRunStep(st => Math.min(order.length - 1, st + 1)); if (s >= order.length - 1) clearInterval(stepInt); }, 520);
    let i = 0;
    timer.current = setInterval(() => {
      i += 3; setStreamText(answer.slice(0, i));
      if (i >= answer.length) { clearInterval(timer.current); setStreaming(false); setMsgs(m => [...m, { who: 'a', text: answer }]); setStreamText(''); }
    }, 22);
  };
  useEffect(() => () => clearInterval(timer.current), []);
  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [msgs, streamText]);

  const activeRun = streaming || runStep >= 0;

  return React.createElement('div', { className: 'col', style: { flex: 1, minHeight: 0 } },
    React.createElement('div', { className: 'row spread', style: { padding: '12px 22px', borderBottom: '1px solid var(--line)', flex: 'none' } },
      React.createElement('div', { className: 'row gap3' },
        React.createElement('div', { style: { position: 'relative' } }, React.createElement('button', { className: 'btn btn-secondary btn-sm' }, React.createElement(Icon, { name: 'workflows', size: 14 }), 'Support Router', React.createElement('span', { className: 'chip chip-mono', style: { height: 18 } }, 'v8 draft'), React.createElement(Icon, { name: 'chevdown', size: 14 }))),
        React.createElement('span', { className: 'chip chip-mono' }, React.createElement(Icon, { name: 'n_llm', size: 12 }), 'claude-sonnet-4-6')),
      React.createElement('div', { className: 'row gap2' },
        React.createElement(Segmented, { options: [{ value: 'chat', label: 'Chat', icon: 'msg' }, { value: 'graph', label: 'Graph', icon: 'workflows' }], value: mode, onChange: setMode }),
        React.createElement('button', { className: 'btn btn-ghost btn-sm' }, React.createElement(Icon, { name: 'refresh', size: 14 }), 'Reset thread'))),
    React.createElement('div', { style: { flex: 1, display: 'grid', gridTemplateColumns: mode === 'chat' ? '1fr 340px' : '1fr 320px', minHeight: 0 } },
      /* MAIN */
      mode === 'chat'
        ? React.createElement('div', { className: 'col', style: { minHeight: 0, borderRight: '1px solid var(--line)' } },
            React.createElement('div', { ref: scrollRef, className: 'scroll-y', style: { flex: 1, padding: '24px 0' } },
              React.createElement('div', { style: { maxWidth: 680, margin: '0 auto', padding: '0 24px' } },
                msgs.map((m, i) => React.createElement(ChatBubble, { key: i, m })),
                streaming && React.createElement(ChatBubble, { m: { who: 'a', text: streamText }, streaming: true }))),
            React.createElement(Composer, { input, setInput, onSend: send, disabled: streaming }))
        : React.createElement('div', { className: 'col', style: { minHeight: 0, borderRight: '1px solid var(--line)', background: 'var(--canvas-bg)', position: 'relative' } },
            React.createElement('div', { style: { position: 'absolute', inset: 0, backgroundImage: 'radial-gradient(var(--canvas-grid) 1px, transparent 1px)', backgroundSize: '22px 22px' } }),
            React.createElement(RunGraph, { activeStep: runStep, order }),
            React.createElement(Composer, { input, setInput, onSend: send, disabled: streaming, embedded: true })),
      /* RIGHT: run inspector */
      React.createElement(RunInspector, { active: activeRun, step: runStep, order, streaming })));
}

function ChatBubble({ m, streaming }) {
  const isA = m.who === 'a';
  return React.createElement('div', { className: 'fade-up', style: { display: 'flex', gap: 12, marginBottom: 22, flexDirection: isA ? 'row' : 'row-reverse' } },
    isA ? React.createElement(Tile, { icon: 'n_agent', color: 'var(--accent)', size: 30 }) : React.createElement(Avatar, { name: 'Riley Cho', size: 30 }),
    React.createElement('div', { style: { maxWidth: 480 } },
      React.createElement('div', { className: 'row gap2', style: { marginBottom: 5, justifyContent: isA ? 'flex-start' : 'flex-end' } }, React.createElement('span', { style: { fontSize: 12, fontWeight: 700 } }, isA ? 'billing_agent' : 'You'), isA && React.createElement('span', { className: 'fg-2 t-caption' }, 'via Support Router')),
      React.createElement('div', { style: { padding: '11px 14px', borderRadius: 13, fontSize: 13.5, lineHeight: '20px', background: isA ? 'var(--bg-2)' : 'var(--accent)', color: isA ? 'var(--fg-0)' : 'var(--fg-on-accent)', border: isA ? '1px solid var(--line)' : 'none', borderTopLeftRadius: isA ? 3 : 13, borderTopRightRadius: isA ? 13 : 3 } },
        m.text, streaming && React.createElement('span', { style: { display: 'inline-block', width: 7, height: 15, background: 'var(--accent)', marginLeft: 2, verticalAlign: '-2px', animation: 'blink 1s step-start infinite' } })),
      isA && !streaming && React.createElement('div', { className: 'row gap2', style: { marginTop: 7 } }, React.createElement('button', { className: 'iconbtn', style: { width: 26, height: 26 } }, React.createElement(Icon, { name: 'copy', size: 13 })), React.createElement('button', { className: 'iconbtn', style: { width: 26, height: 26 } }, React.createElement(Icon, { name: 'traces', size: 13 })), React.createElement('span', { className: 'fg-2 t-caption', style: { alignSelf: 'center' } }, '12.4k tok · 4.2s'))));
}

function Composer({ input, setInput, onSend, disabled, embedded }) {
  return React.createElement('div', { style: { padding: embedded ? 16 : '14px 24px', borderTop: embedded ? 'none' : '1px solid var(--line)', position: embedded ? 'absolute' : 'static', bottom: 0, left: 0, right: 0, zIndex: 5 } },
    React.createElement('div', { style: { maxWidth: 680, margin: '0 auto' } },
      React.createElement('div', { className: 'row gap2', style: { background: 'var(--bg-2)', border: '1px solid var(--line-strong)', borderRadius: 13, padding: '7px 7px 7px 14px', boxShadow: 'var(--sh-1)' } },
        React.createElement('input', { value: input, onChange: e => setInput(e.target.value), onKeyDown: e => e.key === 'Enter' && onSend(), placeholder: 'Message the agent…', style: { flex: 1, border: 'none', outline: 'none', background: 'none', fontSize: 14, color: 'var(--fg-0)', fontFamily: 'var(--font-ui)' } }),
        React.createElement('button', { className: 'iconbtn' }, React.createElement(Icon, { name: 'upload', size: 16 })),
        React.createElement('button', { className: 'btn btn-primary', onClick: onSend, disabled }, React.createElement(Icon, { name: 'play', size: 15 }), 'Run')),
      React.createElement('div', { className: 'fg-2 t-caption', style: { textAlign: 'center', marginTop: 7 } }, 'Runs against the draft graph · interrupts surface here for approval')));
}

/* horizontal run graph for graph mode */
function RunGraph({ activeStep, order }) {
  const nodes = DATA.workflowNodes.filter(n => order.includes(n.id));
  return React.createElement('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap', gap: 0, padding: 40 } },
    React.createElement('div', { className: 'row', style: { gap: 0, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 760 } },
      order.map((id, i) => {
        const n = DATA.workflowNodes.find(x => x.id === id);
        const meta = DATA.NODE_META[n.type] || {};
        const cat = DATA.CAT_BY_TYPE[n.type] || 'control';
        const color = window.WorkflowNodeKit.CAT_COLOR[cat];
        const isActive = activeStep === i, isDone = activeStep > i;
        return React.createElement('div', { key: id, className: 'row', style: { gap: 0 } },
          i > 0 && React.createElement('div', { style: { width: 30, height: 2, background: isDone || isActive ? color : 'var(--line-strong)', margin: '0 2px', transition: 'background .3s', position: 'relative' } },
            isActive && React.createElement('div', { style: { position: 'absolute', top: -2, left: 0, width: 6, height: 6, borderRadius: '50%', background: color, boxShadow: `0 0 8px ${color}`, animation: 'slide 0.5s infinite' } })),
          React.createElement('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '6px 0', opacity: activeStep >= 0 && !isActive && !isDone ? 0.4 : 1, transition: 'opacity .3s' } },
            React.createElement('div', { style: { width: 48, height: 48, borderRadius: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-2)', color, border: `1.5px solid ${isActive || isDone ? color : 'var(--line-strong)'}`, boxShadow: isActive ? `0 0 0 3px color-mix(in srgb,${color} 30%, transparent), 0 0 22px color-mix(in srgb,${color} 50%, transparent)` : 'var(--sh-1)', transition: 'box-shadow .3s, border-color .3s' } },
              isDone ? React.createElement(Icon, { name: 'check', size: 20 }) : React.createElement(Icon, { name: meta.icon, size: 20 })),
            React.createElement('span', { style: { fontSize: 10.5, fontWeight: 600, color: isActive ? 'var(--fg-0)' : 'var(--fg-2)', maxWidth: 64, textAlign: 'center' } }, n.title || meta.label)));
      })));
}

function RunInspector({ active, step, order, streaming }) {
  const [tab, setTab] = useState('steps');
  const events = [
    { t: '+0.00s', ev: 'run.start', d: 'thread_8842' }, { t: '+0.04s', ev: 'node.enter', d: 'faq_deflect' },
    { t: '+0.21s', ev: 'node.exit', d: 'faq_deflect · no match' }, { t: '+0.26s', ev: 'router', d: '→ billing' },
    { t: '+0.33s', ev: 'tool.call', d: 'get_order(8842)' }, { t: '+0.65s', ev: 'tool.result', d: '92 tok (projected)' },
    { t: '+2.1s', ev: 'interrupt', d: 'human approval · refund' },
  ];
  return React.createElement('div', { className: 'col', style: { minHeight: 0, background: 'var(--bg-1)' } },
    React.createElement('div', { style: { padding: '0 14px', borderBottom: '1px solid var(--line)' } }, React.createElement(Tabs, { tabs: [{ value: 'steps', label: 'Steps' }, { value: 'state', label: 'State' }, { value: 'events', label: 'Events' }], value: tab, onChange: setTab })),
    React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 14 } },
      tab === 'steps' && React.createElement('div', { className: 'col gap2' },
        active ? order.map((id, i) => {
          const n = DATA.workflowNodes.find(x => x.id === id); const meta = DATA.NODE_META[n.type] || {};
          const isActive = step === i, isDone = step > i;
          return React.createElement('div', { key: id, className: 'row gap2', style: { padding: '8px 10px', borderRadius: 8, border: '1px solid ' + (isActive ? 'var(--signal)' : 'var(--line)'), background: isActive ? 'var(--signal-glow)' : 'transparent', opacity: !isActive && !isDone && step >= 0 ? 0.5 : 1 } },
            React.createElement('div', { style: { width: 18, height: 18, borderRadius: '50%', flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', background: isDone ? 'var(--ok)' : isActive ? 'var(--signal)' : 'var(--bg-3)', color: isDone || isActive ? '#fff' : 'var(--fg-2)' } }, isDone ? React.createElement(Icon, { name: 'check', size: 11 }) : isActive ? React.createElement('div', { style: { width: 6, height: 6, borderRadius: '50%', background: '#fff', animation: 'pulse 1s infinite' } }) : React.createElement(Icon, { name: meta.icon, size: 10 })),
            React.createElement('span', { className: 'grow', style: { fontSize: 12.5, fontWeight: 600 } }, n.title || meta.label),
            isDone && React.createElement('span', { className: 'mono-sm fg-2' }, (0.2 + i * 0.4).toFixed(1) + 's'));
        }) : React.createElement(EmptyState, { icon: 'playground', title: 'No active run', sub: 'Send a message to watch the graph execute step by step.' })),
      tab === 'state' && React.createElement(CodeBlock, { code: '{\n  "messages": [ … 4 ],\n  "intent": "billing",\n  "order": {\n    "id": "ord_8842",\n    "status": "shipped",\n    "amount": 11000\n  },\n  "refund_pending": true,\n  "_interrupt": "human_approval"\n}' }),
      tab === 'events' && React.createElement('div', { className: 'col gap1' }, events.slice(0, active ? Math.max(1, step + 3) : events.length).map((e, i) => React.createElement('div', { key: i, className: 'row gap2', style: { padding: '6px 8px', borderRadius: 6, fontFamily: 'var(--font-mono)', fontSize: 11.5 } },
        React.createElement('span', { style: { color: 'var(--fg-2)', width: 48, flex: 'none' } }, e.t),
        React.createElement('span', { className: 'typechip', style: { color: e.ev === 'interrupt' ? 'var(--warn)' : 'var(--signal)' } }, e.ev),
        React.createElement('span', { className: 'fg-1 truncate' }, e.d))))));
}

Object.assign(window, { PlaygroundScreen, ChatBubble, Composer, RunGraph, RunInspector });
