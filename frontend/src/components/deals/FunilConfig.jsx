import { useEffect, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash, ArrowUp, ArrowDown } from "@phosphor-icons/react";
import Modal from "@/components/ui/Modal";

// Sugestões de cor da etapa: a paleta da marca primeiro, depois os sinais
// (verde de aprovado, vermelho de não aprovado, amarelo).
const CORES = ["#4F6B78", "#0B6E8A", "#16B3AC", "#F5B841", "#0B2E40", "#10B981", "#EF4444", "#E9C98B"];
const TIPOS = [["aberta", "Em aberto"], ["ganho", "Aprovado"], ["perdido", "Não aprovado"]];

export default function FunilConfig({ pipeline, onClose, onSaved }) {
  const [nome, setNome] = useState(pipeline.nome);
  const [etapas, setEtapas] = useState([]);
  const [moverPara, setMoverPara] = useState("");
  const [conflito, setConflito] = useState(null);

  useEffect(() => {
    setNome(pipeline.nome);
    setEtapas((pipeline.etapas || []).map((e) => ({ ...e })));
    setConflito(null);
    setMoverPara("");
  }, [pipeline]);

  const alterar = (i, campos) =>
    setEtapas((lista) => lista.map((e, j) => (j === i ? { ...e, ...campos } : e)));

  const mover = (i, delta) => {
    const j = i + delta;
    if (j < 0 || j >= etapas.length) return;
    const copia = [...etapas];
    [copia[i], copia[j]] = [copia[j], copia[i]];
    setEtapas(copia);
  };

  const adicionar = () =>
    setEtapas([...etapas, { id: null, nome: "Nova etapa", cor: CORES[0], tipo: "aberta" }]);

  const remover = (i) => setEtapas(etapas.filter((_, j) => j !== i));

  // A trava de clique repetido vem do Modal.
  const salvar = async () => {
    try {
      await api.patch(`/pipelines/${pipeline.id}`,
        { nome, etapas: etapas.map(({ id, nome: n, cor, tipo }) => ({ id, nome: n, cor, tipo })) },
        { params: moverPara ? { mover_para: moverPara } : {} });
      toast.success("Etapas salvas");
      setConflito(null);
      onSaved?.();
      onClose();
    } catch (ex) {
      const detalhe = formatApiErrorDetail(ex.response?.data?.detail);
      // 409 = etapa removida ainda tem negócios. Em vez de só reclamar, mostra
      // o seletor de destino, que é o que resolve.
      if (ex.response?.status === 409) setConflito(detalhe);
      else toast.error(detalhe);
    }
  };

  const restantes = etapas.filter((e) => e.id);

  return (
    <Modal titulo="Etapas dos orçamentos" largo
      descricao="A etapa marcada como Aprovado ou Não aprovado é a que conclui o orçamento e alimenta os relatórios de aprovação."
      onFechar={onClose} onSalvar={salvar} testId="funil-config"
      rodape={(
        <button type="button" onClick={adicionar} className="zz-btn zz-btn-secondary mr-auto"
          data-testid="add-etapa">
          <Plus size={13} weight="regular" /> Adicionar etapa
        </button>
      )}>
      <div className="max-w-sm">
        <label className="text-sm font-medium text-ink-2 block mb-1">
          Nome do quadro
        </label>
        <input required className="zz-input" data-testid="funil-nome"
          value={nome} onChange={(e) => setNome(e.target.value)} />
      </div>

      <div className="border border-line mb-3 rounded-lg">
        {etapas.map((etapa, i) => (
          <div key={etapa.id || `nova-${i}`}
            className="flex items-center gap-2 px-3 py-2 border-b border-line last:border-b-0"
            data-testid={`etapa-row-${i}`}>
            <div className="flex gap-0.5">
              <button type="button" onClick={() => mover(i, -1)} disabled={i === 0}
                className="p-0.5 hover:bg-surface-3 disabled:opacity-20"><ArrowUp size={11} weight="regular" /></button>
              <button type="button" onClick={() => mover(i, 1)} disabled={i === etapas.length - 1}
                className="p-0.5 hover:bg-surface-3 disabled:opacity-20"><ArrowDown size={11} weight="regular" /></button>
            </div>
            <input required className="zz-input h-7 py-0 text-sm flex-1" value={etapa.nome}
              onChange={(ev) => alterar(i, { nome: ev.target.value })} />
            <select className="zz-input h-7 py-0 text-xs w-28" value={etapa.tipo}
              onChange={(ev) => alterar(i, { tipo: ev.target.value })}>
              {TIPOS.map(([v, r]) => <option key={v} value={v}>{r}</option>)}
            </select>
            <div className="flex gap-0.5">
              {CORES.map((c) => (
                <button key={c} type="button" onClick={() => alterar(i, { cor: c })}
                  className={`w-5 h-5 rounded-md border-2 ${etapa.cor === c ? "border-ink" : "border-transparent"}`}
                  style={{ background: c }} />
              ))}
            </div>
            <button type="button" onClick={() => remover(i)} disabled={etapas.length === 1}
              className="p-1 hover:bg-danger-soft text-danger disabled:opacity-20">
              <Trash size={13} weight="regular" />
            </button>
          </div>
        ))}
      </div>

      {conflito && (
        <div className="border border-warning bg-warning-soft p-3 mb-3 rounded-lg">
          <div className="text-sm text-ink mb-2">{conflito}</div>
          <div className="flex gap-2 items-center">
            <select className="zz-input h-8 py-1 text-sm max-w-xs" data-testid="mover-para"
              value={moverPara} onChange={(e) => setMoverPara(e.target.value)}>
              <option value="">Mover os orçamentos para…</option>
              {restantes.map((e) => <option key={e.id} value={e.id}>{e.nome}</option>)}
            </select>
            <button type="submit" disabled={!moverPara} className="zz-btn text-xs py-1 disabled:opacity-40">
              Mover e salvar
            </button>
          </div>
        </div>
      )}

    </Modal>
  );
}
