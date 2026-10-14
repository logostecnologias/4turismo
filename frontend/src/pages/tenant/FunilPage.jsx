import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api, { formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { formatBRL, parseBRLInput } from "@/lib/currency";
import {
  Plus, SlidersHorizontal, TrendUp, MagnifyingGlass, X, User, Warning,
  BellSlash,
} from "@phosphor-icons/react";
import Modal from "@/components/ui/Modal";
import DealPanel from "@/components/deals/DealPanel";
import FunilConfig from "@/components/deals/FunilConfig";
import FunilMetricas from "@/components/deals/FunilMetricas";

function Cartao({ deal, onAbrir }) {
  const atrasado = deal.previsao_fechamento && deal.status === "aberto" &&
    new Date(deal.previsao_fechamento) < new Date();
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", deal.id);
        e.dataTransfer.effectAllowed = "move";
      }}
      onClick={() => onAbrir(deal.id)}
      className="border border-line bg-surface p-3 mb-2 cursor-pointer hover:border-line-strong active:opacity-60 rounded-lg"
      data-testid={`deal-card-${deal.id}`}
    >
      <div className="text-sm font-semibold leading-tight mb-1 line-clamp-2">{deal.titulo}</div>
      <div className="tabular-nums text-sm font-bold mb-1.5">{formatBRL(deal.valor)}</div>
      {deal.contact && (
        <div className="flex items-center gap-1 text-[11px] text-ink-2 truncate">
          <User size={11} weight="regular" className="shrink-0" />
          <span className="truncate">{deal.contact.nome}</span>
        </div>
      )}
      <div className="flex items-center gap-2 mt-1.5">
        {deal.sem_proximo_passo && (
          <span title="Sem próximo passo agendado — abra e agende" className="text-warning">
            <BellSlash size={12} weight="regular" />
          </span>
        )}
        {deal.previsao_fechamento && (
          <span className={`text-[10px] tabular-nums ${atrasado ? "text-danger font-bold" : "text-ink-3"}`}>
            {new Date(deal.previsao_fechamento).toLocaleDateString("pt-BR")}
          </span>
        )}
      </div>
    </div>
  );
}

function SeletorContato({ valor, onChange }) {
  const [busca, setBusca] = useState("");
  const [achados, setAchados] = useState([]);

  useEffect(() => {
    const t = setTimeout(() => {
      api.get("/contacts", { params: { q: busca || undefined, limit: 20 } })
        .then((r) => setAchados(r.data.items || [])).catch(() => setAchados([]));
    }, 300);
    return () => clearTimeout(t);
  }, [busca]);

  if (valor) {
    return (
      <div className="flex items-center gap-2 border border-line bg-surface h-9 px-2 rounded-lg">
        <span className="text-sm font-semibold flex-1 truncate">{valor.nome}</span>
        <button type="button" onClick={() => onChange(null)} className="p-0.5 hover:bg-surface-3">
          <X size={12} weight="regular" />
        </button>
      </div>
    );
  }
  return (
    <div className="border border-line bg-surface rounded-lg">
      <input className="zz-input border-0 h-9" placeholder="Buscar cliente…" data-testid="deal-contact-search"
        value={busca} onChange={(e) => setBusca(e.target.value)} />
      {busca && (
        <div className="max-h-36 overflow-y-auto border-t border-line">
          {achados.length === 0 && <div className="p-2 text-xs text-ink-3">Nada encontrado.</div>}
          {achados.map((c) => (
            <button key={c.id} type="button" onClick={() => { onChange(c); setBusca(""); }}
              className="w-full text-left flex items-center gap-2 px-2 py-1.5 border-b border-line hover:bg-surface-2">
              <span className="text-sm font-semibold flex-1 truncate">{c.nome}</span>
              <span className="text-[11px] tabular-nums text-ink-3">{c.numero_whatsapp}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function FunilPage() {
  const [funis, setFunis] = useState([]);
  const [funilId, setFunilId] = useState("");
  const [quadro, setQuadro] = useState(null);
  const [usuarios, setUsuarios] = useState([]);
  const [busca, setBusca] = useState("");
  const [responsavel, setResponsavel] = useState("");
  const [abertoId, setAbertoId] = useState(null);
  const [config, setConfig] = useState(false);
  const [metricas, setMetricas] = useState(false);
  const [novo, setNovo] = useState(null);
  // Queda numa coluna de "perdido" precisa do motivo antes de gravar — sem ele
  // o relatório de motivos de perda nasce vazio e ninguém volta para preencher.
  const [perda, setPerda] = useState(null);
  const [arrastandoSobre, setArrastandoSobre] = useState(null);
  // Link direto vindo da ficha do contato ("abrir este negócio").
  const [params] = useSearchParams();
  useEffect(() => {
    const id = params.get("negocio");
    if (id) setAbertoId(id);
  }, [params]);

  useEffect(() => {
    api.get("/pipelines").then((r) => {
      setFunis(r.data);
      setFunilId((atual) => atual || r.data[0]?.id || "");
    }).catch(() => {});
    api.get("/users").then((r) => setUsuarios(r.data)).catch(() => {});
  }, []);

  const carregar = useCallback(() => {
    if (!funilId) return;
    api.get("/deals/board", {
      params: { pipeline_id: funilId, q: busca || undefined, responsavel_id: responsavel || undefined },
    }).then((r) => setQuadro(r.data)).catch(() => {});
  }, [funilId, busca, responsavel]);

  useEffect(() => {
    const t = setTimeout(carregar, 250);
    return () => clearTimeout(t);
  }, [carregar]);

  const mover = async (dealId, etapaId, motivo) => {
    try {
      const payload = { etapa_id: etapaId };
      if (motivo !== undefined) payload.motivo_perda = motivo;
      await api.patch(`/deals/${dealId}`, payload);
      carregar();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const soltar = (e, etapa) => {
    e.preventDefault();
    setArrastandoSobre(null);
    const dealId = e.dataTransfer.getData("text/plain");
    if (!dealId) return;
    const coluna = quadro.colunas.find((c) => c.deals.some((d) => d.id === dealId));
    if (coluna?.etapa.id === etapa.id) return;
    if (etapa.tipo === "perdido") setPerda({ dealId, etapaId: etapa.id, motivo: "" });
    else mover(dealId, etapa.id);
  };

  // A trava de clique repetido vem do Modal.
  const criar = async () => {
    if (!novo.contato) return toast.error("Escolha o cliente do orçamento");
    try {
      const r = await api.post("/deals", {
        titulo: novo.titulo,
        contact_id: novo.contato.id,
        pipeline_id: funilId,
        valor: novo.valor,
        responsavel_id: novo.responsavel_id || null,
        previsao_fechamento: novo.previsao || null,
      });
      setNovo(null);
      toast.success("Orçamento criado");
      carregar();
      setAbertoId(r.data.id);
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const funil = quadro?.pipeline;
  const totalAberto = (quadro?.colunas || [])
    .filter((c) => c.etapa.tipo === "aberta")
    .reduce((acc, c) => acc + (c.valor || 0), 0);

  return (
    <div className={`p-8 ${abertoId ? "pr-[500px]" : ""}`} data-testid="funil-page">
      <div className="flex items-end justify-between mb-6 gap-4">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Orçamentos</h1>
          <p className="text-ink-2 mt-2 text-sm">
            Arraste o cartão entre as etapas. {formatBRL(totalAberto)} em aberto.
          </p>
        </div>
        <div className="flex gap-2 items-center">
          {funis.length > 1 && (
            <select className="zz-input max-w-[200px]" data-testid="funil-select"
              value={funilId} onChange={(e) => setFunilId(e.target.value)}>
              {funis.map((f) => <option key={f.id} value={f.id}>{f.nome}</option>)}
            </select>
          )}
          <button onClick={() => { setMetricas((v) => !v); setConfig(false); }}
            className="zz-btn zz-btn-secondary" data-testid="toggle-metricas">
            <TrendUp size={14} weight="regular" /> Desempenho
          </button>
          <button onClick={() => { setConfig((v) => !v); setMetricas(false); }}
            className="zz-btn zz-btn-secondary" data-testid="toggle-config">
            <SlidersHorizontal size={14} weight="regular" /> Etapas
          </button>
          <button onClick={() => setNovo({ titulo: "", valor: 0, contato: null, responsavel_id: "", previsao: "" })}
            className="zz-btn" data-testid="new-deal-btn" disabled={!funilId}>
            <Plus size={14} weight="regular" /> Novo orçamento
          </button>
        </div>
      </div>

      {metricas && funilId && (
        <Modal titulo="Desempenho dos orçamentos" largo onFechar={() => setMetricas(false)}>
          <FunilMetricas pipelineId={funilId} semMoldura />
        </Modal>
      )}
      {config && funil && (
        <FunilConfig pipeline={funil} onClose={() => setConfig(false)}
          onSaved={() => {
            api.get("/pipelines").then((r) => setFunis(r.data)).catch(() => {});
            carregar();
          }} />
      )}

      {novo && (
        <Modal titulo="Novo orçamento"
          descricao="É o orçamento que gera taxa de aprovação, ticket médio e tempo até a aprovação. A conversa sozinha, não."
          onFechar={() => setNovo(null)} onSalvar={criar}
          salvarLabel="Criar" salvandoLabel="Criando…" testId="new-deal-form"
          corpoClasse="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="sm:col-span-2">
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Título</label>
            <input required autoFocus className="zz-input" data-testid="new-deal-title"
              placeholder="Pacote Lençóis 3 dias, Circuito Lagoa Bonita…"
              value={novo.titulo} onChange={(e) => setNovo({ ...novo, titulo: e.target.value })} />
          </div>
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Cliente</label>
            <SeletorContato valor={novo.contato} onChange={(c) => setNovo({ ...novo, contato: c })} />
          </div>
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Valor</label>
            <input className="zz-input tabular-nums" data-testid="new-deal-value"
              value={formatBRL(novo.valor)}
              onChange={(e) => setNovo({ ...novo, valor: parseBRLInput(e.target.value) })} />
          </div>
        </Modal>
      )}

      <div className="flex gap-3 items-center mb-4">
        <div className="relative flex-1 max-w-xs">
          <MagnifyingGlass size={14} weight="regular" className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-4" />
          <input className="zz-input pl-8" placeholder="Buscar orçamento…" data-testid="deal-search"
            value={busca} onChange={(e) => setBusca(e.target.value)} />
        </div>
        <select className="zz-input max-w-[200px]" data-testid="deal-owner-filter"
          value={responsavel} onChange={(e) => setResponsavel(e.target.value)}>
          <option value="">Todos os responsáveis</option>
          {usuarios.map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
        </select>
        {(busca || responsavel) && (
          <button onClick={() => { setBusca(""); setResponsavel(""); }} className="p-1.5 hover:bg-surface-3">
            <X size={14} weight="regular" />
          </button>
        )}
      </div>

      {!quadro ? (
        <div className="text-sm font-medium text-ink-3 py-8">Carregando quadro…</div>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4 items-start">
          {quadro.colunas.map((coluna) => (
            <div key={coluna.etapa.id}
              onDragOver={(e) => { e.preventDefault(); setArrastandoSobre(coluna.etapa.id); }}
              onDragLeave={() => setArrastandoSobre((a) => (a === coluna.etapa.id ? null : a))}
              onDrop={(e) => soltar(e, coluna.etapa)}
              className={`w-[240px] shrink-0 bg-surface-2 border rounded-xl ${
                arrastandoSobre === coluna.etapa.id ? "border-info border-2 bg-info-soft" : "border-line"
              }`}
              data-testid={`funil-coluna-${coluna.etapa.id}`}>
              <div className="px-3 py-2 border-b border-line bg-surface"
                style={{ borderTop: `3px solid ${coluna.etapa.cor}` }}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium truncate">{coluna.etapa.nome}</span>
                  <span className="text-[10px] tabular-nums text-ink-3 shrink-0">{coluna.n}</span>
                </div>
                <div className="tabular-nums text-xs font-bold text-ink">
                  {formatBRL(coluna.valor)}{coluna.valor_parcial ? "+" : ""}
                </div>
              </div>
              <div className="p-2 min-h-[120px] max-h-[calc(100vh-380px)] overflow-y-auto">
                {coluna.deals.length === 0 && (
                  <div className="text-[11px] text-ink-4 text-center py-6">Arraste um cartão para cá</div>
                )}
                {coluna.deals.map((d) => <Cartao key={d.id} deal={d} onAbrir={setAbertoId} />)}
                {coluna.truncada && (
                  <div className="text-[10px] text-ink-3 text-center py-1 border-t border-line">
                    mostrando {coluna.deals.length} de {coluna.n}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {perda && (
        <Modal titulo="Por que não foi aprovado?"
          descricao={'É este campo que alimenta o relatório de motivos de não aprovação. Pode deixar em branco, mas aí o orçamento entra como "não informado".'}
          onFechar={() => setPerda(null)}
          onSalvar={() => { mover(perda.dealId, perda.etapaId, perda.motivo); setPerda(null); }}
          salvarLabel="Marcar como não aprovado" testId="motivo-perda-dialog">
          <div className="flex items-center gap-2 text-danger">
            <Warning size={18} weight="regular" />
            <span className="text-sm font-semibold">Este orçamento sai do quadro como não aprovado.</span>
          </div>
          <input autoFocus className="zz-input" data-testid="motivo-perda-input"
            placeholder="Valor, momento, optou por outra agência…"
            value={perda.motivo} onChange={(e) => setPerda({ ...perda, motivo: e.target.value })} />
        </Modal>
      )}

      {abertoId && (
        <DealPanel dealId={abertoId} pipeline={funil} usuarios={usuarios}
          onClose={() => setAbertoId(null)} onChanged={carregar} />
      )}
    </div>
  );
}
