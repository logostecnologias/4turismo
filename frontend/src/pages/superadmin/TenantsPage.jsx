import { useEffect, useState } from "react";
import api from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { Plus, PauseCircle, PlayCircle, Trash, SignIn } from "@phosphor-icons/react";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import { formatBRL } from "@/lib/currency";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import NovaEmpresa from "@/components/superadmin/NovaEmpresa";

export default function TenantsPage() {
  const [tenants, setTenants] = useState([]);
  const [plans, setPlans] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const nav = useNavigate();
  const { refresh } = useAuth();

  // `destino` existe por causa da criação: depois de criar uma empresa o
  // próximo passo real é ler o QR do WhatsApp, e cair no painel para navegar
  // até lá à mão é onde esse passo se perdia.
  const [impersonate, entrando] = useAcao(async (t, destino = "/app") => {
    try {
      await api.post(`/admin/tenants/${t.id}/impersonate`);
      await refresh();
      nav(destino);
    } catch (ex) {
      toast.error(ex.response?.data?.detail || "Erro ao entrar na agência");
    }
  });

  const load = () => api.get("/admin/tenants").then((r) => setTenants(r.data));
  const loadPlans = () => api.get("/admin/plans").then((r) => setPlans(r.data));
  useEffect(() => { load(); loadPlans(); }, []);

  const changePlan = async (t, planId) => {
    if (!planId || planId === t.plan_id) return;
    try {
      const { data } = await api.patch(`/admin/tenants/${t.id}`, { plan_id: planId });
      if (data.plan_warnings?.length) {
        toast.warning(`Plano alterado, mas a agência está acima do limite: ${data.plan_warnings.join(" · ")}`, { duration: 8000 });
      } else {
        toast.success("Plano atualizado");
      }
      load();
    } catch (ex) {
      toast.error(ex.response?.data?.detail || "Erro ao atualizar plano");
    }
  };

  const toggle = async (t) => {
    const next = t.status === "active" ? "suspended" : "active";
    await api.patch(`/admin/tenants/${t.id}`, { status: next });
    load();
  };

  const [remove, removendo] = useAcao(async (id) => {
    if (!(await confirmToast("Excluir a agência e TODOS os dados vinculados?", { confirmLabel: "Excluir tudo" }))) return;
    const { data } = await api.delete(`/admin/tenants/${id}`);
    // O que saiu, somado. A exclusão não deixa rastro em lugar nenhum depois
    // de rodar, e é o único momento em que dá para conferir que ela alcançou o
    // que devia.
    const n = Object.values(data?.apagados || {}).reduce((a, b) => a + b, 0);
    toast.success(n ? `Agência excluída, ${n} registros removidos` : "Agência excluída");
    load();
  });

  return (
    <div className="zz-page" data-testid="admin-tenants-page">
      <div className="flex items-end justify-between mb-8 gap-4">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Agências</h1>
          <p className="text-ink-2 mt-2 text-sm">
            {tenants.length === 0
              ? "Cada agência é um ambiente isolado, com o próprio admin, plano e conexões."
              : `${tenants.length} ${tenants.length === 1 ? "agência" : "agências"}, ` +
                `${tenants.filter((t) => t.status === "active").length} ativa(s).`}
          </p>
        </div>
        <button data-testid="new-tenant-btn" onClick={() => setShowForm(true)} className="zz-btn shrink-0">
          <Plus size={16} weight="regular" /> Nova agência
        </button>
      </div>

      {showForm && (
        <NovaEmpresa
          plans={plans}
          onFechar={() => setShowForm(false)}
          onCriada={load}
          onEntrar={(t, destino) => impersonate(t, destino)}
        />
      )}

      <div className="border border-line bg-surface rounded-lg">
        <div className="grid grid-cols-12 px-4 py-2 border-b border-line text-sm font-medium text-ink-3">
          <div className="col-span-3">Nome</div>
          <div className="col-span-3">Plano</div>
          <div className="col-span-2">Status</div>
          <div className="col-span-1">Fluxos</div>
          <div className="col-span-2">Criado em</div>
          <div className="col-span-1 text-right">Ações</div>
        </div>
        {tenants.length === 0 && <div className="p-6 text-center text-sm text-ink-3">Nenhuma agência cadastrada.</div>}
        {tenants.map((t) => (
          <div key={t.id} className="grid grid-cols-12 px-4 py-3 border-b border-line items-center hover:bg-surface-2" data-testid={`tenant-row-${t.id}`}>
            <div className="col-span-3 font-semibold">{t.name}</div>
            <div className="col-span-3 pr-3">
              {plans.length === 0 ? (
                <span className="text-xs italic text-ink-4">nenhum plano cadastrado</span>
              ) : (
                <select
                  className="zz-input text-xs py-1"
                  data-testid={`tenant-plan-${t.id}`}
                  value={t.plan_id || ""}
                  onChange={(e) => changePlan(t, e.target.value)}
                >
                  {!t.plan_id && <option value="" disabled>sem plano</option>}
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>{p.nome} — {formatBRL(p.preco_mensal)}</option>
                  ))}
                </select>
              )}
            </div>
            <div className="col-span-2">
              <span className={`text-xs font-medium px-2 py-1 border rounded-full ${t.status === "active" ? "bg-success-soft border-success text-success" : "bg-danger-soft border-danger text-danger"}`}>{t.status === "active" ? "ativa" : "suspensa"}</span>
            </div>
            <div className="col-span-1 tabular-nums text-sm">{t.bots_count}</div>
            <div className="col-span-2 text-xs text-ink-3 tabular-nums">{new Date(t.created_at).toLocaleDateString("pt-BR")}</div>
            <div className="col-span-1 flex gap-1 justify-end">
              <button onClick={() => impersonate(t)} disabled={entrando}
                className="p-1.5 hover:bg-info-soft text-info" title="Entrar na agência" data-testid={`impersonate-${t.id}`}><SignIn size={16} weight="regular" /></button>
              <button onClick={() => toggle(t)} className="p-1.5 hover:bg-surface-3" title={t.status === "active" ? "Suspender" : "Reativar"}>
                {t.status === "active" ? <PauseCircle size={16} weight="regular" /> : <PlayCircle size={16} weight="regular" />}
              </button>
              <button onClick={() => remove(t.id)} disabled={removendo}
                className="p-1.5 hover:bg-danger-soft text-danger" title="Excluir"><Trash size={16} weight="regular" /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
