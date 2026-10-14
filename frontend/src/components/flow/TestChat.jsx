import { useState, useRef, useEffect } from "react";
import api from "@/lib/api";
import { X, PaperPlaneRight, ArrowClockwise, List, Warning } from "@phosphor-icons/react";
import { FORMATO_BOTOES, FORMATO_LISTA, FORMATO_TEXTO } from "./nodeTypes";

export default function TestChat({ flowId, onClose }) {
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef(null);

  const startSim = async () => {
    setLoading(true);
    setMessages([]);
    try {
      const { data } = await api.post(`/flows/${flowId}/test`, { start: true });
      setSessionId(data.session_id);
      applyOutputs(data.outputs);
    } catch (e) {
      setMessages([{ role: "system", text: "Erro ao iniciar simulação" }]);
    } finally { setLoading(false); }
  };

  useEffect(() => { if (flowId) startSim(); /* eslint-disable-next-line */ }, [flowId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const applyOutputs = (outputs) => {
    const items = outputs.map((o, i) => {
      if (o.type === "text") return { role: "bot", text: o.content };
      // O menu vem inteiro no output: formato, título, rodapé e as opções com
      // id. O simulador desenha o formato configurado porque é ele que a pessoa
      // precisa conferir antes de publicar — ver a lista numerada não prova que
      // os botões vão sair certos.
      if (o.type === "menu") return { role: "bot", text: o.content, menu: o };
      if (o.type === "media") return { role: "bot", text: `[${o.media_type}] ${o.url}${o.caption ? " — " + o.caption : ""}` };
      if (o.type === "system") return { role: "system", text: o.content };
      if (o.type === "delay") return { role: "system", text: `⏱ Aguardando ${o.seconds}s...` };
      return { role: "system", text: JSON.stringify(o) };
    });
    setMessages((m) => [...m, ...items]);
  };

  // `escolhaId` é o id da opção quando o teste toca num botão em vez de digitar.
  // Vai separado do texto porque é assim que a resposta real chega do WhatsApp:
  // o rótulo pode vir cortado, o id nunca.
  const send = async (text, escolhaId = null) => {
    const msg = (text ?? input).trim();
    if (!msg && !escolhaId) return;
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setInput("");
    setLoading(true);
    try {
      const { data } = await api.post(`/flows/${flowId}/test`,
        { session_id: sessionId, message: msg, escolha_id: escolhaId });
      applyOutputs(data.outputs);
      if (data.status === "finalizada") {
        setMessages((m) => [...m, { role: "system", text: "— Conversa encerrada —" }]);
      }
    } catch (e) {
      setMessages((m) => [...m, { role: "system", text: "Erro no motor de fluxo" }]);
    } finally { setLoading(false); }
  };

  return (
    <div className="w-[380px] border-l border-line-strong bg-surface flex flex-col h-full" data-testid="test-chat">
      <div className="flex items-center justify-between px-4 py-3 border-b border-line-strong bg-brand text-brand-fg">
        <div>
          <div className="text-sm font-medium opacity-75">Simulação</div>
          <div className="font-heading text-lg font-semibold tracking-tight">Testar Fluxo</div>
        </div>
        <div className="flex gap-2">
          <button data-testid="restart-test" onClick={startSim} className="p-1.5 hover:bg-brand" title="Reiniciar">
            <ArrowClockwise size={16} weight="regular" />
          </button>
          <button data-testid="close-test-chat" onClick={onClose} className="p-1.5 hover:bg-brand">
            <X size={16} weight="regular" />
          </button>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 bg-surface-2">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[80%] px-3 py-2 text-sm border rounded-2xl ${
                m.role === "user"
                  ? "bg-success-soft border-success text-ink"
                  : m.role === "system"
                  ? "bg-surface border-line text-ink-3 italic text-xs mx-auto"
                  : "bg-surface border-line text-ink"
              }`}
            >
              {m.menu?.titulo && <div className="font-bold whitespace-pre-wrap">{m.menu.titulo}</div>}
              <div className="whitespace-pre-wrap">{m.text}</div>
              {m.menu?.rodape && <div className="text-[11px] text-ink-3 mt-1">{m.menu.rodape}</div>}
              {m.menu && <MenuSimulado menu={m.menu} onEscolher={send} />}
            </div>
          </div>
        ))}
        {loading && <div className="text-xs text-ink-3 text-center">processando...</div>}
      </div>

      <div className="p-3 border-t border-line flex gap-2">
        <input
          data-testid="test-chat-input"
          className="zz-input" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Digite uma mensagem..." disabled={loading}
        />
        <button data-testid="test-chat-send" onClick={() => send()} disabled={loading || !input} className="zz-btn">
          <PaperPlaneRight size={14} weight="regular" />
        </button>
      </div>
    </div>
  );
}

// Desenha o menu no formato que o contato vai receber. Tocar numa opção manda o
// id dela, não o número: é o caminho que um botão real exercita, e é o único
// que prova que a saída do nó está ligada na opção certa.
function MenuSimulado({ menu, onEscolher }) {
  const [aberta, setAberta] = useState(false);
  const formato = menu.formato || FORMATO_TEXTO;
  const itens = menu.itens || [];
  const problemas = menu.problemas || [];

  // Configuração fora dos tetos do WhatsApp: quem envia degrada para texto
  // numerado, e o simulador mostra o mesmo — junto com o motivo, que é a
  // informação que falta para consertar.
  const degradado = problemas.length > 0;
  const efetivo = degradado ? FORMATO_TEXTO : formato;

  const opcao = (item, i) => (
    <button
      key={item.id || i}
      onClick={() => onEscolher(item.label, item.id)}
      className="block w-full text-left text-xs px-2 py-1.5 bg-surface-2 border border-line-strong hover:border-info rounded-lg"
      data-testid={`menu-option-${i}`}
    >
      {efetivo === FORMATO_TEXTO ? `${i + 1}. ` : ""}{item.label}
      {item.descricao && <div className="text-[10px] text-ink-3">{item.descricao}</div>}
    </button>
  );

  return (
    <div className="mt-2 space-y-1">
      {degradado && (
        <div className="text-[10px] text-danger flex gap-1 items-start" data-testid="menu-degradado">
          <Warning size={12} weight="regular" style={{ flexShrink: 0, marginTop: 1 }} />
          <span>Sai como texto numerado: {problemas.join(" · ")}</span>
        </div>
      )}

      {efetivo === FORMATO_LISTA ? (
        <>
          <button
            onClick={() => setAberta((v) => !v)}
            className="flex items-center justify-center gap-1.5 w-full text-xs px-2 py-1.5 border border-line-strong text-info font-medium rounded-lg hover:border-info"
            data-testid="menu-abrir-lista"
          >
            <List size={13} weight="regular" /> {menu.botao_texto || "Ver opções"}
          </button>
          {aberta && (
            <div className="space-y-1 pt-1">
              {menu.titulo_secao && (
                <div className="text-sm font-medium text-ink-3">{menu.titulo_secao}</div>
              )}
              {itens.map(opcao)}
            </div>
          )}
        </>
      ) : (
        <div className={efetivo === FORMATO_BOTOES ? "flex flex-wrap gap-1" : "space-y-1"}>
          {efetivo === FORMATO_BOTOES
            ? itens.map((item, i) => (
                <button
                  key={item.id || i}
                  onClick={() => onEscolher(item.label, item.id)}
                  className="flex-1 min-w-[30%] text-xs px-2 py-1.5 border border-line-strong text-info font-medium rounded-lg hover:border-info"
                  data-testid={`menu-option-${i}`}
                >
                  {item.label}
                </button>
              ))
            : itens.map(opcao)}
        </div>
      )}
    </div>
  );
}
