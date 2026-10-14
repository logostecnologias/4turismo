import { useRef, useState } from "react";
import api, { API, formatApiErrorDetail } from "@/lib/api";
import { ImageSquare, Plus, Trash, Check, X } from "@phosphor-icons/react";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";

// Catálogo de fotos que o agente pode mandar sozinho.
//
// A descrição não é legenda: é a **única** coisa que o modelo sabe sobre a
// imagem. Ele é de texto e não enxerga o arquivo — quem escreve "Onix 2019
// prata, 45 mil km, frente" recebe a foto na hora certa; quem escreve "foto 3"
// não. É por isso que o campo é obrigatório e que o texto de ajuda insiste
// nisso em vez de dizer só "descrição".

const MAX_FOTOS = 30;

export default function FotosAgente({ agente, onMudou }) {
  const [fotos, setFotos] = useState(() => agente?.midias || []);
  const [arquivo, setArquivo] = useState(null);
  const [previa, setPrevia] = useState(null);
  const [descricao, setDescricao] = useState("");
  const [subindo, setSubindo] = useState(false);
  const [editando, setEditando] = useState(null); // {id, texto}
  const inputArquivo = useRef(null);

  // Agente ainda não salvo não tem id, e o upload precisa de um.
  if (!agente?.id) {
    return (
      <div className="rounded-xl border border-dashed border-line-strong p-6 text-center">
        <ImageSquare size={24} weight="regular" className="mx-auto text-ink-4 mb-2" />
        <p className="text-sm text-ink-2">
          As fotos entram depois de criar o assistente.
        </p>
        <p className="text-[11px] text-ink-3 mt-1">
          Termine os passos, clique em criar e abra a configuração de novo para montar o catálogo.
        </p>
      </div>
    );
  }

  const escolher = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setArquivo(f);
    setPrevia(URL.createObjectURL(f));
  };

  const limpar = () => {
    if (previa) URL.revokeObjectURL(previa);
    setArquivo(null);
    setPrevia(null);
    setDescricao("");
    if (inputArquivo.current) inputArquivo.current.value = "";
  };

  const enviar = async () => {
    if (!arquivo || !descricao.trim() || subindo) return;
    setSubindo(true);
    const corpo = new FormData();
    corpo.append("file", arquivo);
    corpo.append("descricao", descricao.trim());
    try {
      const { data } = await api.post(`/ai/agents/${agente.id}/midias`, corpo);
      setFotos((f) => [...f, data]);
      limpar();
      onMudou?.();
      toast.success("Foto adicionada ao catálogo.");
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setSubindo(false);
    }
  };

  const salvarDescricao = async () => {
    const { id, texto } = editando;
    if (!texto.trim()) return;
    try {
      await api.patch(`/ai/agents/${agente.id}/midias/${id}`, { descricao: texto.trim() });
      setFotos((f) => f.map((x) => (x.id === id ? { ...x, descricao: texto.trim() } : x)));
      setEditando(null);
      onMudou?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const remover = async (foto) => {
    if (!(await confirmToast(`Tirar "${foto.descricao}" do catálogo do assistente?`,
      { confirmLabel: "Tirar" }))) return;
    try {
      await api.delete(`/ai/agents/${agente.id}/midias/${foto.id}`);
      setFotos((f) => f.filter((x) => x.id !== foto.id));
      onMudou?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const cheio = fotos.length >= MAX_FOTOS;

  return (
    <div data-testid="fotos-agente">
      <div className="space-y-2 mb-3">
        {fotos.map((f, i) => (
          <div key={f.id} className="flex items-start gap-3 rounded-xl border border-line p-2.5">
            <img
              src={`${API}/media/${f.media_id}`}
              alt={f.descricao}
              className="w-16 h-16 rounded-lg object-cover bg-surface-3 shrink-0"
            />
            <div className="flex-1 min-w-0">
              <div className="text-[10px] tabular-nums text-ink-4 mb-1">[FOTO:{i + 1}]</div>
              {editando?.id === f.id ? (
                <div className="flex gap-2">
                  <input
                    className="zz-input text-sm py-1.5"
                    autoFocus
                    value={editando.texto}
                    onChange={(e) => setEditando({ ...editando, texto: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") salvarDescricao();
                      if (e.key === "Escape") setEditando(null);
                    }}
                  />
                  <button className="zz-btn text-xs py-1.5 shrink-0" onClick={salvarDescricao}>
                    <Check size={14} weight="regular" />
                  </button>
                </div>
              ) : (
                <button
                  className="text-sm text-left hover:text-info w-full"
                  onClick={() => setEditando({ id: f.id, texto: f.descricao })}
                  title="Clique para corrigir a descrição"
                >
                  {f.descricao}
                </button>
              )}
            </div>
            <button
              className="w-8 h-8 rounded-lg flex items-center justify-center text-ink-3 hover:bg-danger-soft hover:text-danger shrink-0"
              onClick={() => remover(f)}
              aria-label="Tirar do catálogo"
            >
              <Trash size={15} />
            </button>
          </div>
        ))}
      </div>

      {arquivo ? (
        <div className="rounded-xl border border-info bg-info-soft p-3 flex items-start gap-3">
          <img src={previa} alt="" className="w-16 h-16 rounded-lg object-cover shrink-0" />
          <div className="flex-1 min-w-0">
            <input
              className="zz-input text-sm"
              autoFocus
              placeholder="Descreva a foto: Lagoa Azul vista de cima, dunas ao pôr do sol"
              value={descricao}
              onChange={(e) => setDescricao(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && enviar()}
              data-testid="foto-descricao"
            />
            <div className="flex gap-2 mt-2">
              <button className="zz-btn text-xs py-1.5" disabled={!descricao.trim() || subindo}
                onClick={enviar} data-testid="foto-enviar">
                {subindo ? "Enviando…" : "Adicionar"}
              </button>
              <button className="zz-btn zz-btn-secondary text-xs py-1.5" onClick={limpar}>
                <X size={13} weight="regular" /> Cancelar
              </button>
            </div>
          </div>
        </div>
      ) : (
        <button
          type="button"
          disabled={cheio}
          onClick={() => inputArquivo.current?.click()}
          data-testid="foto-escolher"
          className="w-full rounded-xl border border-dashed border-line-strong py-4 flex items-center
                     justify-center gap-2 text-ink-3 hover:border-info hover:text-info
                     hover:bg-info-soft disabled:opacity-45 transition-colors"
        >
          <Plus size={18} weight="regular" />
          <span className="text-sm font-bold">
            {cheio ? `Limite de ${MAX_FOTOS} fotos atingido` : "Adicionar foto"}
          </span>
        </button>
      )}

      <input ref={inputArquivo} type="file" accept="image/*" className="hidden" onChange={escolher} />

      <p className="text-[11px] text-ink-3 mt-2">
        O assistente <strong>não vê</strong> a imagem: ele decide pela descrição. Escreva como se
        estivesse contando por telefone o que aparece na foto: lugar, passeio, detalhe, ângulo.
        {fotos.length > 0 && ` ${fotos.length} de ${MAX_FOTOS}.`}
      </p>
    </div>
  );
}
