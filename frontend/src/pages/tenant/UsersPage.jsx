import { useEffect, useState } from "react";
import api from "@/lib/api";
import Modal from "@/components/ui/Modal";
import { Plus, Trash, PencilSimple } from "@phosphor-icons/react";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import { rotuloPapel } from "@/lib/papeis";

export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [connections, setConnections] = useState([]);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: "", email: "", password: "", role: "agent", connection_ids: [] });

  const load = async () => {
    const [u, c] = await Promise.all([api.get("/users"), api.get("/whatsapp/connections")]);
    setUsers(u.data);
    setConnections(c.data);
  };
  useEffect(() => { load(); }, []);

  const openNew = () => { setForm({ name: "", email: "", password: "", role: "agent", connection_ids: [] }); setEditing({}); };
  const openEdit = (u) => {
    setForm({ name: u.name, email: u.email, password: "", role: u.role, connection_ids: u.connection_ids || [] });
    setEditing(u);
  };

  const toggleConn = (cid) => {
    setForm((f) => ({
      ...f,
      connection_ids: f.connection_ids.includes(cid)
        ? f.connection_ids.filter((x) => x !== cid)
        : [...f.connection_ids, cid],
    }));
  };

  // A trava de clique repetido vem do Modal.
  const save = async () => {
    try {
      if (editing?.id) {
        const payload = { name: form.name, connection_ids: form.connection_ids };
        if (form.password) payload.password = form.password;
        await api.patch(`/users/${editing.id}`, payload);
      } else {
        await api.post("/users", form);
      }
      setEditing(null);
      toast.success("Usuário salvo");
      load();
    } catch (ex) {
      toast.error(ex.response?.data?.detail || "Erro ao salvar");
    }
  };

  const remove = async (id) => {
    if (!(await confirmToast("Excluir usuário?", { confirmLabel: "Excluir" }))) return;
    await api.delete(`/users/${id}`);
    toast.success("Usuário excluído");
    load();
  };

  return (
    <div className="zz-page" data-testid="users-page">
      <div className="flex items-end justify-between mb-8">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Usuários</h1>
          <p className="text-ink-2 mt-2 text-sm">Cadastre atendentes e defina quais conexões cada um pode ver.</p>
        </div>
        <button data-testid="new-user-btn" onClick={openNew} className="zz-btn"><Plus size={14} weight="regular" /> Novo Usuário</button>
      </div>

      {editing && (
        <Modal titulo={editing?.id ? "Editar usuário" : "Novo usuário"}
          descricao="Atendente só vê as conversas das conexões liberadas para ele."
          onFechar={() => setEditing(null)} onSalvar={save} testId="user-form"
          corpoClasse="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Nome"><input required className="zz-input" data-testid="user-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Email"><input required type="email" disabled={!!editing?.id} className="zz-input" data-testid="user-email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label={editing?.id ? "Nova senha (opcional)" : "Senha"}>
            <input required={!editing?.id} className="zz-input" data-testid="user-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
          <Field label="Papel">
            <select className="zz-input" value={form.role} disabled={!!editing?.id} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="agent">Atendente</option>
              <option value="tenant_admin">Admin</option>
            </select>
          </Field>
          <div className="sm:col-span-2">
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Conexões liberadas</label>
            {connections.length === 0 ? (
              <div className="text-xs text-ink-3 italic">Nenhuma conexão de WhatsApp criada ainda.</div>
            ) : (
              <div className="space-y-1">
                {connections.map((c) => (
                  <label key={c.id} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="checkbox" checked={form.connection_ids.includes(c.id)} onChange={() => toggleConn(c.id)} data-testid={`conn-check-${c.id}`} />
                    {c.nome} · <span className="tabular-nums text-ink-3">{c.status}</span>
                  </label>
                ))}
              </div>
            )}
            {form.role === "tenant_admin" && (
              <div className="text-xs text-ink-3 mt-1">Admins ignoram esta restrição (acesso total).</div>
            )}
          </div>
        </Modal>
      )}

      <div className="border border-line bg-surface rounded-lg">
        <div className="grid grid-cols-12 px-4 py-2 border-b border-line text-sm font-medium text-ink-3">
          <div className="col-span-4">Nome</div>
          <div className="col-span-3">Email</div>
          <div className="col-span-2">Papel</div>
          <div className="col-span-2">Conexões</div>
          <div className="col-span-1 text-right">Ações</div>
        </div>
        {users.length === 0 && <div className="p-6 text-sm text-ink-3 text-center">Nenhum usuário.</div>}
        {users.map((u) => (
          <div key={u.id} className="grid grid-cols-12 px-4 py-3 border-b border-line items-center hover:bg-surface-2" data-testid={`user-row-${u.id}`}>
            <div className="col-span-4 font-semibold">{u.name}</div>
            <div className="col-span-3 tabular-nums text-xs text-ink">{u.email}</div>
            <div className="col-span-2"><span className="text-xs font-medium px-2 py-1 border rounded-full bg-surface-2 border-line text-ink">{rotuloPapel(u.role)}</span></div>
            <div className="col-span-2 tabular-nums text-xs">{u.role === "tenant_admin" ? "todas" : (u.connection_ids?.length || 0)}</div>
            <div className="col-span-1 flex gap-1 justify-end">
              <button onClick={() => openEdit(u)} className="p-1.5 hover:bg-surface-3"><PencilSimple size={14} weight="regular" /></button>
              <button onClick={() => remove(u.id)} className="p-1.5 hover:bg-danger-soft text-danger"><Trash size={14} weight="regular" /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label className="text-sm font-medium text-ink-2 block mb-1.5">{label}</label>
      {children}
    </div>
  );
}
