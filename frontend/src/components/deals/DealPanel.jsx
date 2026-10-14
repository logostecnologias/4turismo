import { useCallback, useEffect, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import { formatBRL, parseBRLInput } from "@/lib/currency";
import Atividades from "@/components/activities/Atividades";
import {
  X, Trash, User, ClockCounterClockwise, ChatCircleDots,
} from "@phosphor-icons/react";

const EVENTO_LABEL = {
  criado: "criou o orçamento",
  etapa: "moveu de etapa",
  valor: "mudou o valor",
  ganho: "marcou como aprovado",
  perdido: "marcou como não aprovado",
};

function Linha({ label, children }) {
  return (
    <div className="grid grid-cols-3 gap-2 items-center py-1">
      <label className="text-sm font-medium text-ink-3">{label}</label>
      <div className="col-span-2">{children}</div>
    </div>
  );
}

export default function DealPanel({ dealId, pipeline, usuarios, onClose, onChanged }) {
  const [dados, setDados] = useState(null);
  const [form, setForm] = useState(null);
  const [salvando, setSalvando] = useState(false);

  const carregar = useCallback(() => {
    api.get(`/deals/${dealId}`).then((r) => {
      setDados(r.data);
      const d = r.data.deal;
      setForm({
        titulo: d.titulo || "",
        valor: d.valor || 0,
        etapa_id: d.etapa_id || "",
        responsavel_id: d.responsavel_id || "",
        previsao_fechamento: (d.previsao_fechamento || "").slice(0, 10),
        origem: d.origem || "",
        motivo_perda: d.motivo_perda || "",
      });
    }).catch((ex) => {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
      onClose();
    });
  }, [dealId, onClose]);

  useEffect(() => { carregar(); }, [carregar]);

  const salvar = async () => {
    setSalvando(true);
    try {
      const payload = {
        titulo: form.titulo,
        valor: form.valor,
        etapa_id: form.etapa_id,
        responsavel_id: form.responsavel_id || null,
        previsao_fechamento: form.previsao_fechamento || null,
        origem: form.origem,
      };
      // O motivo só vai quando o negócio está (ou vai ficar) perdido — o
      // backend recusa motivo em negócio ganho, e mandar sempre faria um
      // salvamento comum falhar.
      const etapa = (pipeline?.etapas || []).find((e) => e.id === form.etapa_id);
      if (etapa?.tipo === "perdido") payload.motivo_perda = form.motivo_perda;
      await api.patch(`/deals/${dealId}`, payload);
      toast.success("Orçamento salvo");
      carregar();
      onChanged?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setSalvando(false);
    }
  };

  const excluir = async () => {
    if (!(await confirmToast(`Excluir o orçamento "${dados.deal.titulo}"?`,
      { confirmLabel: "Excluir" }))) return;
    try {
      await api.delete(`/deals/${dealId}`);
      toast.success("Orçamento excluído");
      onChanged?.();
      onClose();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  if (!dados || !form) {
    return (
      <aside className="fixed right-0 top-0 h-screen w-[480px] bg-surface border-l border-line shadow-pop z-40 flex items-center justify-center">
        <div className="text-sm font-medium text-ink-3">Carregando…</div>
      </aside>
    );
  }

  const d = dados.deal;
  const etapaAtual = (pipeline?.etapas || []).find((e) => e.id === form.etapa_id);

  return (
    <aside className="fixed right-0 top-0 h-screen w-[480px] bg-surface border-l border-line shadow-pop z-40 flex flex-col"
      data-testid="deal-panel">
      <div className="px-5 py-4 border-b border-line-strong flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-ink-3 mb-1">
            {d.status === "ganho" ? "Aprovado" : d.status === "perdido" ? "Não aprovado" : "Em aberto"}
          </div>
          <div className="font-heading text-xl font-semibold truncate">{d.titulo}</div>
          <div className="tabular-nums text-sm text-ink">{formatBRL(d.valor)}</div>
        </div>
        <button onClick={onClose} data-testid="deal-panel-close" className="p-1.5 hover:bg-surface-3">
          <X size={16} weight="regular" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {dados.contact && (
          <div className="px-5 py-3 border-b border-line flex items-center gap-2">
            <User size={14} weight="regular" className="text-ink-3" />
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold truncate">{dados.contact.nome}</div>
              <div className="text-[11px] tabular-nums text-ink-3">
                {dados.contact.numero_whatsapp}
                {dados.contact.empresa ? ` · ${dados.contact.empresa}` : ""}
              </div>
            </div>
            <a href={`/app/contacts?contato=${dados.contact.id}`} className="zz-btn zz-btn-secondary text-xs py-1">
              Ficha
            </a>
          </div>
        )}

        <div className="px-5 py-4">
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-medium text-ink-3">Dados</div>
            <button onClick={salvar} disabled={salvando} className="zz-btn text-xs py-1 disabled:opacity-40"
              data-testid="deal-save">{salvando ? "Salvando…" : "Salvar"}</button>
          </div>
          <Linha label="Título">
            <input className="zz-input h-8 py-1 text-sm" data-testid="deal-field-titulo"
              value={form.titulo} onChange={(e) => setForm({ ...form, titulo: e.target.value })} />
          </Linha>
          <Linha label="Valor">
            <input className="zz-input h-8 py-1 text-sm tabular-nums" data-testid="deal-field-valor"
              value={formatBRL(form.valor)}
              onChange={(e) => setForm({ ...form, valor: parseBRLInput(e.target.value) })} />
          </Linha>
          <Linha label="Etapa">
            <select className="zz-input h-8 py-1 text-sm" data-testid="deal-field-etapa"
              value={form.etapa_id} onChange={(e) => setForm({ ...form, etapa_id: e.target.value })}>
              {(pipeline?.etapas || []).map((e) => <option key={e.id} value={e.id}>{e.nome}</option>)}
            </select>
          </Linha>
          {etapaAtual?.tipo === "perdido" && (
            <Linha label="Motivo da não aprovação">
              <input className="zz-input h-8 py-1 text-sm" data-testid="deal-field-motivo"
                placeholder="Valor, momento, optou por outra agência…"
                value={form.motivo_perda}
                onChange={(e) => setForm({ ...form, motivo_perda: e.target.value })} />
            </Linha>
          )}
          <Linha label="Responsável">
            <select className="zz-input h-8 py-1 text-sm" data-testid="deal-field-responsavel"
              value={form.responsavel_id}
              onChange={(e) => setForm({ ...form, responsavel_id: e.target.value })}>
              <option value="">Sem responsável</option>
              {usuarios.map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
            </select>
          </Linha>
          <Linha label="Previsão">
            <input type="date" className="zz-input h-8 py-1 text-sm" data-testid="deal-field-previsao"
              value={form.previsao_fechamento}
              onChange={(e) => setForm({ ...form, previsao_fechamento: e.target.value })} />
          </Linha>
          <Linha label="Origem">
            <input className="zz-input h-8 py-1 text-sm" placeholder="indicação, Instagram, site…"
              value={form.origem} onChange={(e) => setForm({ ...form, origem: e.target.value })} />
          </Linha>
          {d.fechado_em && (
            <Linha label="Decidido em">
              <span className="text-sm tabular-nums text-ink-2">
                {new Date(d.fechado_em).toLocaleDateString("pt-BR")}
                {d.ciclo_dias != null ? ` · ${d.ciclo_dias} dias até a decisão` : ""}
              </span>
            </Linha>
          )}
        </div>

        <div className="px-5 py-4 border-t border-line">
          <div className="text-sm font-medium text-ink-3 mb-3">
            Próximos passos
          </div>
          <Atividades vinculo={{ deal_id: dealId }} onMudou={onChanged} />
        </div>

        <div className="px-5 py-4 border-t border-line">
          <div className="text-sm font-medium text-ink-3 mb-3 flex items-center gap-1.5">
            <ClockCounterClockwise size={13} weight="regular" /> Histórico
          </div>
          <div className="space-y-1.5">
            {dados.eventos.map((ev) => (
              <div key={ev.id} className="border-l-2 border-line pl-3 py-1" data-testid={`deal-event-${ev.id}`}>
                <div className="text-xs text-ink">
                  <strong className="font-semibold">{ev.autor_nome || "sistema"}</strong>{" "}
                  {EVENTO_LABEL[ev.tipo] || ev.tipo}
                  {ev.de && ev.para ? `: ${ev.de} → ${ev.para}` : ev.para ? `: ${ev.para}` : ""}
                  {ev.valor_para != null ? `: ${formatBRL(ev.valor_para)}` : ""}
                  {ev.motivo ? ` (${ev.motivo})` : ""}
                </div>
                <div className="text-[10px] tabular-nums text-ink-3">
                  {new Date(ev.created_at).toLocaleString("pt-BR")}
                </div>
              </div>
            ))}
          </div>
        </div>

        {d.session_id && (
          <div className="px-5 pb-4">
            <a href={`/app/inbox?session=${d.session_id}`}
              className="flex items-center gap-2 border border-line px-3 py-2 hover:bg-surface-2 text-xs text-ink rounded-lg">
              <ChatCircleDots size={14} weight="regular" className="text-ink-3" />
              Abrir a conversa que originou este orçamento
            </a>
          </div>
        )}
      </div>

      <div className="border-t border-line p-3 flex justify-end">
        <button onClick={excluir} data-testid="deal-delete"
          className="zz-btn zz-btn-secondary text-xs text-danger border-danger hover:bg-danger-soft">
          <Trash size={13} weight="regular" /> Excluir orçamento
        </button>
      </div>
    </aside>
  );
}
