import { useCallback, useEffect, useState, useRef } from "react";
import api from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { useAuth } from "@/context/AuthContext";
import { useActiveSession } from "@/hooks/useActiveSession";
import { Hand, CheckCircle, X, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import { STATUS, SessionListItem, MessageBubble, Avatar, ChatComposer, fotoDoContato } from "@/components/atendimento/shared";

export default function InboxPage() {
  const { user } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [stage, setStage] = useState("aguardando");
  const [activeId, setActiveId] = useState(null);
  const chatEnd = useRef(null);
  const { active, messages, refresh: refreshActive } = useActiveSession(activeId);

  const loadSessions = useCallback(
    () => api.get("/atendimento/sessions").then((r) => setSessions(r.data)),
    [],
  );

  useEffect(() => { loadSessions(); }, [loadSessions]);
  useEffect(() => {
    const t = setInterval(() => {
      loadSessions();
      refreshActive();
    }, 3000);
    return () => clearInterval(t);
  }, [loadSessions, refreshActive]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length]);

  const counts = STATUS.reduce((acc, s) => {
    acc[s.key] = sessions.filter((x) => x.status_atendimento === s.key).length;
    return acc;
  }, {});
  const visible = sessions.filter((s) => s.status_atendimento === stage);

  const [assume, assumindo] = useAcao(async () => {
    await api.post(`/atendimento/sessions/${activeId}/assume`);
    toast.success("Conversa assumida");
    setStage("em_atendimento");
    await refreshActive(); loadSessions();
  });
  const [finalize, finalizando] = useAcao(async () => {
    await api.post(`/atendimento/sessions/${activeId}/finalize`);
    toast.success("Atendimento finalizado");
    await refreshActive(); loadSessions();
  });
  const canSend = active?.status_atendimento === "em_atendimento" &&
    (user?.role !== "agent" || active?.atendente_id === user?.id);

  return (
    <div className="h-full flex flex-col" data-testid="inbox-page">
      <div className="border-b border-line-strong bg-surface px-6 py-4">
        <h1 className="font-heading text-2xl font-semibold mb-4">Atendimento</h1>
        <div className="flex items-center gap-2 flex-wrap">
          {STATUS.map((s) => {
            const Icon = s.icon;
            const isActive = stage === s.key;
            const n = counts[s.key] || 0;
            // A fila humana é a única aba que "grita": conversa parada em
            // aguardando é cliente esperando resposta, não pode passar batido.
            const alerting = s.key === "aguardando" && n > 0;
            return (
              <button
                key={s.key}
                onClick={() => { setStage(s.key); setActiveId(null); }}
                data-testid={`stage-btn-${s.key}`}
                className={`flex items-center gap-2 px-3 py-2 border rounded-lg font-medium text-xs transition-colors ${alerting && !isActive ? "zz-alert-pulse" : ""}`}
                style={
                  alerting
                    // Aba selecionada continua distinguível pela borda escura,
                    // mesmo quando o vermelho de alerta assume o fundo.
                    ? { background: "var(--danger-solid)", borderColor: isActive ? "var(--text)" : "var(--danger)", color: "var(--danger-fg)" }
                    : isActive
                      ? { background: s.color, borderColor: s.color, color: "#fff" }
                      : { background: "var(--surface)", borderColor: "var(--line)", color: "var(--text-3)" }
                }
              >
                <Icon size={14} weight="regular" />
                {s.label}
                <span
                  className={`inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 text-[11px] font-bold ${alerting ? "rounded-full bg-surface text-danger" : "tabular-nums border rounded-full"}`}
                  style={alerting ? undefined : { borderColor: isActive ? "#fff" : "var(--line-strong)" }}
                >
                  {n}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {counts.aguardando > 0 && stage !== "aguardando" && (
        <button
          onClick={() => { setStage("aguardando"); setActiveId(null); }}
          data-testid="queue-banner"
          className="w-full shrink-0 flex items-center justify-center gap-2 px-6 py-2 bg-danger-solid text-danger-fg text-sm font-bold border-b border-danger-solid hover:bg-danger zz-alert-blink"
        >
          <Warning size={16} weight="fill" />
          {counts.aguardando === 1
            ? "1 cliente aguardando atendimento"
            : `${counts.aguardando} clientes aguardando atendimento`}
          <span className="underline">— ver fila</span>
        </button>
      )}

      <div className="flex-1 flex overflow-hidden">
        <div className="w-80 border-r border-line bg-surface flex flex-col">
          <div className="flex-1 overflow-y-auto">
            {visible.length === 0 && (
              <div className="text-xs text-ink-4 text-center pt-6">Nenhuma conversa aqui.</div>
            )}
            {visible.map((s) => (
              <SessionListItem key={s.id} session={s} active={activeId === s.id} onClick={() => setActiveId(s.id)} />
            ))}
          </div>
        </div>

        {activeId && active ? (
          <div className="flex-1 bg-surface flex flex-col" data-testid="chat-panel">
            <div className="p-3 border-b border-line-strong flex items-start justify-between gap-3">
              <div className="flex items-start gap-3 min-w-0">
                <Avatar name={active.contact?.nome || active.contact?.numero_whatsapp || "—"} photoUrl={fotoDoContato(active.contact)} size={40} />
                <div className="min-w-0">
                  <div className="font-heading text-lg font-semibold tracking-tight truncate">{active.contact?.nome || "—"}</div>
                  <div className="text-xs tabular-nums text-ink-3 truncate">{active.contact?.numero_whatsapp}</div>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {["aguardando"].includes(active.status_atendimento) && (
                  <button onClick={assume} disabled={assumindo}
                    className="zz-btn text-xs" data-testid="assume-btn">
                    <Hand size={12} weight="regular" /> Assumir
                  </button>
                )}
                {active.status_atendimento === "em_atendimento" && (
                  <button onClick={finalize} disabled={finalizando}
                    className="zz-btn zz-btn-secondary text-xs" data-testid="finalize-btn">
                    <CheckCircle size={12} weight="regular" /> Finalizar
                  </button>
                )}
                <button onClick={() => setActiveId(null)} className="p-1.5 hover:bg-surface-2" data-testid="close-chat"><X size={16} weight="regular" /></button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-2 bg-surface-2">
              {messages.map((m) => (
                <MessageBubble key={m.id} m={m} />
              ))}
              <div ref={chatEnd} />
            </div>

            <ChatComposer sessionId={activeId} canSend={canSend} onSent={refreshActive} />
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-ink-3 bg-surface">
            <div className="text-center">
              <div>Selecione uma conversa para começar a atender</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
