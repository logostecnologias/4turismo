import { useEffect, useState } from "react";
import api from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import Modal from "@/components/ui/Modal";
import { Plus, Trash, PencilSimple } from "@phosphor-icons/react";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";

const DEFAULT_COLORS = ["#0B6E8A", "#16B3AC", "#F5B841", "#0B2E40", "#10B981", "#EF4444", "#E9C98B", "#4F6B78"];

export default function DepartamentosPage() {
  const [depts, setDepts] = useState([]);
  const [editing, setEditing] = useState(null); // null | {} for new, {id,...} for edit
  const [form, setForm] = useState({ nome: "", cor: DEFAULT_COLORS[0] });

  const load = () => api.get("/departments").then((r) => setDepts(r.data));
  useEffect(() => { load(); }, []);

  const openNew = () => { setForm({ nome: "", cor: DEFAULT_COLORS[0] }); setEditing({}); };
  const openEdit = (d) => { setForm({ nome: d.nome, cor: d.cor }); setEditing(d); };

  // A trava de clique repetido vem do Modal.
  const save = async () => {
    try {
      if (editing?.id) await api.patch(`/departments/${editing.id}`, form);
      else await api.post("/departments", form);
      setEditing(null);
      toast.success("Departamento salvo");
      load();
    } catch (ex) {
      toast.error(ex.response?.data?.detail || "Erro ao salvar");
    }
  };

  const [remove, removendo] = useAcao(async (id) => {
    if (!(await confirmToast("Excluir departamento?", { confirmLabel: "Excluir" }))) return;
    await api.delete(`/departments/${id}`);
    toast.success("Departamento excluído");
    load();
  });

  return (
    <div className="zz-page" data-testid="departments-page">
      <div className="flex items-end justify-between mb-8">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Departamentos</h1>
          <p className="text-ink-2 mt-2 text-sm">Agrupe conexões e conversas por área (ex: Vendas, Suporte, Financeiro).</p>
        </div>
        <button data-testid="new-dept-btn" onClick={openNew} className="zz-btn"><Plus size={14} weight="regular" /> Novo</button>
      </div>

      {editing && (
        <Modal titulo={editing?.id ? "Editar departamento" : "Novo departamento"}
          onFechar={() => setEditing(null)} onSalvar={save} testId="dept-form">
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Nome</label>
            <input required autoFocus className="zz-input" data-testid="dept-name" value={form.nome} onChange={(e) => setForm({ ...form, nome: e.target.value })} />
          </div>
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Cor</label>
            <div className="flex gap-2 flex-wrap">
              {DEFAULT_COLORS.map((c) => (
                <button key={c} type="button" onClick={() => setForm({ ...form, cor: c })}
                  className={`w-7 h-7 rounded-md border-2 ${form.cor === c ? "border-ink" : "border-line"}`}
                  style={{ background: c }} />
              ))}
            </div>
          </div>
        </Modal>
      )}

      <div className="border border-line bg-surface rounded-lg">
        {depts.length === 0 && <div className="p-6 text-sm text-ink-3 text-center">Nenhum departamento cadastrado.</div>}
        {depts.map((d) => (
          <div key={d.id} className="flex items-center justify-between px-4 py-3 border-b border-line hover:bg-surface-2" data-testid={`dept-row-${d.id}`}>
            <div className="flex items-center gap-3">
              <div className="w-4 h-4 border border-line-strong rounded-sm" style={{ background: d.cor }} />
              <span className="font-semibold">{d.nome}</span>
              <span className="text-xs tabular-nums text-ink-3">{d.cor}</span>
            </div>
            <div className="flex gap-1">
              <button onClick={() => openEdit(d)} className="p-1.5 hover:bg-surface-3"><PencilSimple size={14} weight="regular" /></button>
              <button onClick={() => remove(d.id)} disabled={removendo}
                className="p-1.5 hover:bg-danger-soft text-danger"><Trash size={14} weight="regular" /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
