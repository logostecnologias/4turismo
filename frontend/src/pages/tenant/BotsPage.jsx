import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import Modal from "@/components/ui/Modal";
import { Plus, PencilSimple, Copy, Trash, Pause, Play } from "@phosphor-icons/react";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import { rotuloGatilho } from "@/components/flow/nodeTypes";

export default function BotsPage() {
  const [bots, setBots] = useState([]);
  const [connections, setConnections] = useState([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const nav = useNavigate();

  const load = () => api.get("/bots").then((r) => setBots(r.data));
  useEffect(() => {
    load();
    api.get("/whatsapp/connections").then((r) => setConnections(r.data)).catch(() => {});
  }, []);

  // A trava de clique repetido vem do Modal.
  const create = async () => {
    if (!name.trim()) return;
    try {
      const { data } = await api.post("/bots", { name, connection_id: connectionId || null });
      setName(""); setConnectionId(""); setCreating(false);
      toast.success("Fluxo criado");
      nav(`/app/bots/${data.id}/flow/${data.main_flow_id}`);
    } catch (ex) {
      toast.error(ex.response?.data?.detail || "Erro ao criar fluxo");
    }
  };

  const [duplicate, duplicando] = useAcao(async (id) => {
    try {
      await api.post(`/bots/${id}/duplicate`);
      toast.success("Fluxo duplicado");
      load();
    } catch (ex) {
      toast.error(ex.response?.data?.detail || "Erro ao duplicar fluxo");
    }
  });

  const toggleStatus = async (bot) => {
    const next = bot.status === "ativo" ? "pausado" : "ativo";
    await api.patch(`/bots/${bot.id}`, { status: next });
    load();
  };

  const [remove, removendo] = useAcao(async (id) => {
    if (!(await confirmToast("Excluir este fluxo automático?", { confirmLabel: "Excluir" }))) return;
    await api.delete(`/bots/${id}`);
    toast.success("Fluxo excluído");
    load();
  });

  return (
    <div className="zz-page" data-testid="bots-page">
      <div className="flex items-end justify-between mb-8">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Fluxos automáticos</h1>
        </div>
        <button data-testid="new-bot-btn" onClick={() => setCreating(true)} className="zz-btn">
          <Plus size={16} weight="regular" /> Criar fluxo
        </button>
      </div>

      {creating && (
        <Modal titulo="Criar fluxo automático"
          descricao="O fluxo automático é criado em branco, e o editor abre em seguida."
          onFechar={() => setCreating(false)} onSalvar={create}
          salvarLabel="Criar" salvandoLabel="Criando…" testId="new-bot-modal">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium text-ink-2 block mb-1.5">Nome do fluxo</label>
              <input autoFocus required data-testid="new-bot-name" className="zz-input" value={name}
                onChange={(e) => setName(e.target.value)} placeholder="Ex: Boas-vindas e agendamento" />
            </div>
            <div>
              <label className="text-sm font-medium text-ink-2 block mb-1.5">Conexão</label>
              <select className="zz-input" data-testid="new-bot-connection" value={connectionId}
                onChange={(e) => setConnectionId(e.target.value)}>
                <option value="">— nenhuma —</option>
                {connections.map((c) => <option key={c.id} value={c.id}>{c.nome}</option>)}
              </select>
            </div>
          </div>
        </Modal>
      )}

      {bots.length === 0 ? (
        <div className="border border-dashed border-line rounded-xl bg-surface p-16 text-center">
          <div className="font-heading text-2xl font-semibold mb-2">Nenhum fluxo automático ainda.</div>
          <div className="text-sm text-ink-2 mb-4">Crie seu primeiro fluxo para começar a automatizar conversas.</div>
        </div>
      ) : (
        <div className="border border-line bg-surface rounded-lg">
          <div className="grid grid-cols-12 px-4 py-2 border-b border-line text-sm font-medium text-ink-3">
            <div className="col-span-4">Nome</div>
            <div className="col-span-2">Gatilho</div>
            <div className="col-span-2">Conexão</div>
            <div className="col-span-2">Status</div>
            <div className="col-span-2 text-right">Ações</div>
          </div>
          {bots.map((b) => (
            <div key={b.id} className="grid grid-cols-12 px-4 py-3 border-b border-line items-center hover:bg-surface-2" data-testid={`bot-row-${b.id}`}>
              <div className="col-span-4 font-semibold text-ink cursor-pointer" onClick={() => nav(`/app/bots/${b.id}/flow/${b.main_flow_id}`)}>
                {b.name}
              </div>
              <div className="col-span-2 text-xs" data-testid={`bot-trigger-${b.id}`}>
                {b.trigger === "no_response"
                  ? <span className="text-ink-3">{rotuloGatilho(b.trigger)}</span>
                  : rotuloGatilho(b.trigger)}
              </div>
              <div className="col-span-2 text-xs" data-testid={`bot-connection-${b.id}`}>
                {b.connection?.nome || <span className="italic text-warning">sem conexão</span>}
              </div>
              <div className="col-span-2">
                <span className={`text-xs font-medium px-2 py-1 border rounded-full ${
                  b.status === "ativo" ? "bg-success-soft border-success text-success" :
                  b.status === "pausado" ? "bg-warning-soft border-warning text-warning" :
                  "bg-surface-2 border-line text-ink-2"
                }`}>{b.status}</span>
              </div>
              <div className="col-span-2 flex gap-1 justify-end">
                <button onClick={() => nav(`/app/bots/${b.id}/flow/${b.main_flow_id}`)} className="p-1.5 hover:bg-surface-3" title="Editar" data-testid={`bot-edit-${b.id}`}><PencilSimple size={14} weight="regular" /></button>
                <button onClick={() => toggleStatus(b)} className="p-1.5 hover:bg-surface-3" title={b.status === "ativo" ? "Pausar" : "Ativar"}>
                  {b.status === "ativo" ? <Pause size={14} weight="regular" /> : <Play size={14} weight="regular" />}
                </button>
                <button onClick={() => duplicate(b.id)} disabled={duplicando}
                  className="p-1.5 hover:bg-surface-3" title="Duplicar"><Copy size={14} weight="regular" /></button>
                <button onClick={() => remove(b.id)} disabled={removendo}
                  className="p-1.5 hover:bg-danger-soft text-danger" title="Excluir"><Trash size={14} weight="regular" /></button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
