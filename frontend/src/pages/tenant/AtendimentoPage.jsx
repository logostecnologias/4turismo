import { useCallback, useEffect, useState, useRef } from "react";
import api from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { useAuth } from "@/context/AuthContext";
import { useActiveSession } from "@/hooks/useActiveSession";
import { Hand, CheckCircle, X } from "@phosphor-icons/react";
import { toast } from "sonner";
import { STATUS, SessionCard, MessageBubble, StatusBadge, ConnectionBadge, ChatComposer } from "@/components/atendimento/shared";

export default function AtendimentoPage() {
  const { user } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [depts, setDepts] = useState([]);
  const [connections, setConnections] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [deptFilter, setDeptFilter] = useState("");
  const [connFilter, setConnFilter] = useState("");
  const chatEnd = useRef(null);
  const { active, messages, refresh: refreshActive } = useActiveSession(activeId);

  const loadSessions = useCallback(() => {
    const params = {};
    if (deptFilter) params.department_id = deptFilter;
    if (connFilter) params.connection_id = connFilter;
    return api.get("/atendimento/sessions", { params }).then((r) => setSessions(r.data));
  }, [deptFilter, connFilter]);

  // Listas que não mudam com a navegação.
  useEffect(() => {
    api.get("/departments").then((r) => setDepts(r.data));
    api.get("/whatsapp/connections").then((r) => setConnections(r.data)).catch(() => {});
  }, []);
  // Carga inicial e recarga a cada troca de filtro — `loadSessions` só muda
  // quando um dos filtros muda.
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

  const [assume, assumindo] = useAcao(async () => {
    await api.post(`/atendimento/sessions/${activeId}/assume`);
    toast.success("Conversa assumida");
    await refreshActive(); loadSessions();
  });
  const [finalize, finalizando] = useAcao(async () => {
    await api.post(`/atendimento/sessions/${activeId}/finalize`);
    toast.success("Atendimento finalizado");
    await refreshActive(); loadSessions();
  });
  const reassign = async (deptId) => {
    await api.patch(`/atendimento/sessions/${activeId}/department`, { department_id: deptId || null });
    toast.success("Departamento reatribuído");
    await refreshActive(); loadSessions();
  };
  const canSend = active?.status_atendimento === "em_atendimento" &&
    (user?.role !== "agent" || active?.atendente_id === user?.id);

  return (
    <div className="h-full flex flex-col" data-testid="atendimento-page">
      <div className="border-b border-line-strong bg-surface px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="font-heading text-2xl font-semibold">Conversas</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium text-ink-3">Conexão:</label>
            <select data-testid="connection-filter" className="zz-input" style={{ width: "auto" }} value={connFilter} onChange={(e) => setConnFilter(e.target.value)}>
              <option value="">Todas</option>
              {connections.map((c) => <option key={c.id} value={c.id}>{c.nome}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium text-ink-3">Depto:</label>
            <select data-testid="dept-filter" className="zz-input" style={{ width: "auto" }} value={deptFilter} onChange={(e) => setDeptFilter(e.target.value)}>
              <option value="">Todos</option>
              {depts.map((d) => <option key={d.id} value={d.id}>{d.nome}</option>)}
            </select>
          </div>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Kanban */}
        <div className="flex-1 grid grid-cols-4 gap-0 overflow-hidden">
          {STATUS.map((col) => {
            const Icon = col.icon;
            const list = sessions.filter((s) => s.status_atendimento === col.key);
            const alerting = col.key === "aguardando" && list.length > 0;
            return (
              <div key={col.key} className="border-r border-line flex flex-col min-w-0 bg-canvas" data-testid={`column-${col.key}`}>
                <div
                  className="px-3 py-2.5 border-b flex items-center justify-between"
                  style={alerting
                    ? { background: "var(--danger-solid)", borderBottomColor: "var(--danger)", color: "var(--danger-fg)" }
                    : { background: "var(--surface)", borderBottomColor: "var(--line)" }}
                >
                  <div className="flex items-center gap-2">
                    <div
                      className="w-5 h-5 flex items-center justify-center border text-white rounded-sm"
                      style={alerting
                        ? { background: "var(--danger)", borderColor: "var(--danger-fg)" }
                        : { background: col.color, borderColor: "var(--line-strong)" }}
                    >
                      <Icon size={12} weight="regular" />
                    </div>
                    <div className="text-sm font-medium">{col.label}</div>
                  </div>
                  <div
                    className={alerting
                      ? "zz-queue-badge zz-alert-pulse"
                      : "text-xs tabular-nums font-bold"}
                    style={alerting ? { background: "var(--danger-fg)", color: "var(--danger-solid)" } : undefined}
                    data-testid={`count-${col.key}`}
                  >
                    {counts[col.key] || 0}
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto p-2 space-y-2">
                  {list.length === 0 && <div className="text-xs text-ink-4 text-center pt-4">Vazio</div>}
                  {list.map((s) => (
                    <SessionCard key={s.id} session={s} active={activeId === s.id} onClick={() => setActiveId(s.id)} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {/* Right chat pane */}
        {activeId && active && (
          <div className="w-[420px] border-l border-line-strong bg-surface flex flex-col" data-testid="chat-panel">
            <div className="p-3 border-b border-line-strong flex items-start justify-between">
              <div className="min-w-0">
                <div className="font-heading text-lg font-semibold tracking-tight truncate">{active.contact?.nome || "—"}</div>
                <div className="text-xs tabular-nums text-ink-3 truncate">{active.contact?.numero_whatsapp}</div>
                <div className="mt-2 flex items-center gap-2 flex-wrap">
                  <StatusBadge status={active.status_atendimento} />
                  {active.connection && <ConnectionBadge connection={active.connection} />}
                  {active.department && (
                    <span className="text-xs font-medium px-1.5 py-0.5 border border-line-strong text-white rounded-full" style={{ background: active.department.cor }}>
                      {active.department.nome}
                    </span>
                  )}
                  {active.agent && (
                    <span className="text-[10px] text-ink-3">👤 {active.agent.name}</span>
                  )}
                </div>
              </div>
              <button onClick={() => setActiveId(null)} className="p-1.5 hover:bg-surface-2" data-testid="close-chat"><X size={16} weight="regular" /></button>
            </div>

            <div className="px-3 py-2 border-b border-line flex items-center gap-2">
              <label className="text-sm font-medium text-ink-3">Depto:</label>
              <select className="zz-input" style={{ padding: "0.3rem 0.5rem" }} value={active.departamento_id || ""} onChange={(e) => reassign(e.target.value)} data-testid="reassign-dept">
                <option value="">— nenhum —</option>
                {depts.map((d) => <option key={d.id} value={d.id}>{d.nome}</option>)}
              </select>
              <div className="flex-1" />
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
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-2 bg-surface-2">
              {messages.map((m) => (
                <MessageBubble key={m.id} m={m} />
              ))}
              <div ref={chatEnd} />
            </div>

            <ChatComposer sessionId={activeId} canSend={canSend} onSent={refreshActive} />
          </div>
        )}
      </div>
    </div>
  );
}
