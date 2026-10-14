import { useEffect, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import { Plus, Trash, PencilSimple } from "@phosphor-icons/react";
import Modal from "@/components/ui/Modal";

const TIPOS = [
  ["texto", "Texto"],
  ["numero", "Número"],
  ["data", "Data"],
  ["selecao", "Lista de opções"],
];

const VAZIO = { nome: "", tipo: "texto", opcoes: [], ordem: 0 };

export default function FieldsManager({ onClose, onChanged }) {
  const [campos, setCampos] = useState([]);
  const [form, setForm] = useState(null);
  const [opcoesTexto, setOpcoesTexto] = useState("");

  const carregar = () => api.get("/contacts/fields").then((r) => setCampos(r.data)).catch(() => {});
  useEffect(() => { carregar(); }, []);

  const abrirNovo = () => {
    setForm({ ...VAZIO, ordem: campos.length });
    setOpcoesTexto("");
  };

  const abrirEdicao = (c) => {
    setForm({ id: c.id, nome: c.nome, tipo: c.tipo, opcoes: c.opcoes || [], ordem: c.ordem || 0 });
    setOpcoesTexto((c.opcoes || []).join(", "));
  };

  // A trava de clique repetido vem do Modal.
  const salvar = async () => {
    const payload = {
      nome: form.nome,
      tipo: form.tipo,
      opcoes: form.tipo === "selecao"
        ? opcoesTexto.split(",").map((o) => o.trim()).filter(Boolean)
        : [],
      ordem: Number(form.ordem) || 0,
    };
    try {
      if (form.id) await api.patch(`/contacts/fields/${form.id}`, payload);
      else await api.post("/contacts/fields", payload);
      setForm(null);
      toast.success("Campo salvo");
      carregar();
      onChanged?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const [remover, removendo] = useAcao(async (c) => {
    if (!(await confirmToast(
      `Excluir o campo "${c.nome}"? O valor dele sai de todas as fichas.`,
      { confirmLabel: "Excluir" }))) return;
    try {
      await api.delete(`/contacts/fields/${c.id}`);
      toast.success("Campo excluído");
      carregar();
      onChanged?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  });

  return (
    <Modal titulo="Campos personalizados"
      descricao="Aparecem na ficha de todo cliente desta agência. Ex: hotel em Barreirinhas, passeio de interesse, data da viagem."
      onFechar={onClose} testId="fields-manager"
      rodape={(
        <button type="button" onClick={abrirNovo} className="zz-btn zz-btn-secondary mr-auto"
          data-testid="new-field-btn">
          <Plus size={13} weight="regular" /> Novo campo
        </button>
      )}>
      {form && (
        <Modal titulo={form.id ? "Editar campo" : "Novo campo"}
          onFechar={() => setForm(null)} onSalvar={salvar} testId="field-form"
          corpoClasse="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1">Nome</label>
            <input required autoFocus className="zz-input" data-testid="field-name"
              value={form.nome} onChange={(e) => setForm({ ...form, nome: e.target.value })} />
          </div>
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1">Tipo</label>
            <select className="zz-input" data-testid="field-type"
              value={form.tipo} onChange={(e) => setForm({ ...form, tipo: e.target.value })}>
              {TIPOS.map(([v, r]) => <option key={v} value={v}>{r}</option>)}
            </select>
          </div>
          {form.tipo === "selecao" && (
            <div className="sm:col-span-2">
              <label className="text-sm font-medium text-ink-2 block mb-1">
                Opções (separadas por vírgula)
              </label>
              <input required className="zz-input" data-testid="field-options"
                placeholder="Individual, Grupo, Cortesia"
                value={opcoesTexto} onChange={(e) => setOpcoesTexto(e.target.value)} />
            </div>
          )}
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1">Ordem</label>
            <input type="number" className="zz-input" value={form.ordem}
              onChange={(e) => setForm({ ...form, ordem: e.target.value })} />
          </div>
        </Modal>
      )}

      <div className="border border-line rounded-lg">
        {campos.length === 0 && (
          <div className="p-4 text-center text-xs text-ink-3">
            Nenhum campo personalizado. A ficha usa só os campos padrão.
          </div>
        )}
        {campos.map((c) => (
          <div key={c.id} className="flex items-center gap-3 px-3 py-2 border-b border-line last:border-b-0 hover:bg-surface-2"
            data-testid={`field-row-${c.id}`}>
            <span className="font-semibold text-sm flex-1">{c.nome}</span>
            <span className="text-sm font-medium text-ink-3">
              {TIPOS.find(([v]) => v === c.tipo)?.[1] || c.tipo}
            </span>
            {c.tipo === "selecao" && (
              <span className="text-[10px] font-mono text-ink-3 truncate max-w-[200px]">
                {(c.opcoes || []).join(" · ")}
              </span>
            )}
            <button onClick={() => abrirEdicao(c)} className="p-1 hover:bg-surface-3">
              <PencilSimple size={13} weight="regular" />
            </button>
            <button onClick={() => remover(c)} disabled={removendo}
              className="p-1 hover:bg-danger-soft text-danger">
              <Trash size={13} weight="regular" />
            </button>
          </div>
        ))}
      </div>
    </Modal>
  );
}
