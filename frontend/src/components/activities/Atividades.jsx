import { useCallback, useEffect, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import {
  Check, Plus, Trash, Phone, ChatText, Users, ListChecks, ArrowCounterClockwise,
} from "@phosphor-icons/react";
import Modal from "@/components/ui/Modal";

export const TIPOS = [
  ["ligacao", "Ligação", Phone],
  ["mensagem", "Mensagem", ChatText],
  ["reuniao", "Reunião", Users],
  ["tarefa", "Tarefa", ListChecks],
];

export function IconeTipo({ tipo, size = 13 }) {
  const Icone = (TIPOS.find(([v]) => v === tipo) || TIPOS[3])[2];
  return <Icone size={size} weight="regular" />;
}

/** "hoje 14:30", "amanhã 09:00", "12/09 16:00" — o formato que se lê de relance. */
export function quando(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const hoje = new Date();
  const dia = (x) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  const amanha = new Date(hoje);
  amanha.setDate(amanha.getDate() + 1);
  const hora = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (dia(d) === dia(hoje)) return `hoje ${hora}`;
  if (dia(d) === dia(amanha)) return `amanhã ${hora}`;
  return `${d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} ${hora}`;
}

/** Sugestão de data para o campo: agora + N dias, no formato do datetime-local. */
export function emDias(dias, hora = 9) {
  const d = new Date();
  d.setDate(d.getDate() + dias);
  if (dias > 0) d.setHours(hora, 0, 0, 0);
  else d.setHours(d.getHours() + 1, 0, 0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// A trava de clique repetido (inclusive o Enter apertado duas vezes) vem do
// Modal, que embrulha o submit.
function FormAtividade({ inicial, onSalvar, onCancelar, rotulo = "Criar",
                         titulo = "Nova tarefa", descricao, extra }) {
  const [form, setForm] = useState(inicial);
  return (
    <Modal titulo={titulo} descricao={descricao} onFechar={onCancelar}
      onSalvar={() => onSalvar(form)} salvarLabel={rotulo}
      rodape={extra} testId="atividade-form">
      <div>
        <label className="text-sm font-medium text-ink-2 block mb-1.5">
          O que precisa ser feito
        </label>
        <input required autoFocus className="zz-input" data-testid="atividade-titulo"
          placeholder="Ligar para confirmar o orçamento…"
          value={form.titulo} onChange={(e) => setForm({ ...form, titulo: e.target.value })} />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">Tipo</label>
          <select className="zz-input" data-testid="atividade-tipo"
            value={form.tipo} onChange={(e) => setForm({ ...form, tipo: e.target.value })}>
            {TIPOS.map(([v, r]) => <option key={v} value={v}>{r}</option>)}
          </select>
        </div>
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">Quando</label>
          <input required type="datetime-local" className="zz-input"
            data-testid="atividade-vence"
            value={form.vence_em} onChange={(e) => setForm({ ...form, vence_em: e.target.value })} />
        </div>
      </div>
      <div className="flex gap-1.5">
        {[[1, "amanhã"], [3, "3 dias"], [7, "1 semana"]].map(([d, r]) => (
          <button key={d} type="button" onClick={() => setForm({ ...form, vence_em: emDias(d) })}
            className="text-xs font-medium px-2 py-1 border border-line bg-surface hover:bg-surface-2 rounded-full">
            {r}
          </button>
        ))}
      </div>
    </Modal>
  );
}

export default function Atividades({ vinculo = {}, onMudou, mostrarVinculos = false }) {
  const [itens, setItens] = useState([]);
  const [concluidas, setConcluidas] = useState([]);
  const [verConcluidas, setVerConcluidas] = useState(false);
  const [criando, setCriando] = useState(false);
  // Conclusão em duas etapas: o passo seguinte é perguntado na hora em que a
  // pessoa está pensando no assunto. "Depois eu agendo" é como se perde a venda.
  const [concluindo, setConcluindo] = useState(null);

  const chave = JSON.stringify(vinculo);
  const carregar = useCallback(() => {
    const params = { ...JSON.parse(chave), limit: 50 };
    api.get("/activities", { params }).then((r) => setItens(r.data.items || [])).catch(() => {});
    api.get("/activities", { params: { ...params, concluidas: true, limit: 20 } })
      .then((r) => setConcluidas(r.data.items || [])).catch(() => {});
  }, [chave]);

  useEffect(() => { carregar(); }, [carregar]);

  const atualizou = () => { carregar(); onMudou?.(); };

  const criar = async (form) => {
    try {
      await api.post("/activities", { ...form, ...vinculo });
      setCriando(false);
      atualizou();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const concluir = async (id, proxima) => {
    try {
      const r = await api.post(`/activities/${id}/complete`, proxima ? { proxima } : {});
      setConcluindo(null);
      toast.success(r.data.proxima ? "Concluída e próximo passo agendado" : "Concluída");
      atualizou();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const reabrir = async (id) => {
    try {
      await api.post(`/activities/${id}/reopen`);
      atualizou();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const excluir = async (a) => {
    if (!(await confirmToast(`Excluir "${a.titulo}"?`, { confirmLabel: "Excluir" }))) return;
    await api.delete(`/activities/${a.id}`);
    atualizou();
  };

  return (
    <div data-testid="atividades">
      <div className="flex items-center justify-between mb-2">
        <div className="flex gap-1.5">
          {!criando && (
            <button onClick={() => setCriando(true)} className="zz-btn text-xs py-1"
              data-testid="nova-atividade">
              <Plus size={12} weight="regular" /> Tarefa
            </button>
          )}
          {concluidas.length > 0 && (
            <button onClick={() => setVerConcluidas((v) => !v)}
              className="zz-btn zz-btn-secondary text-xs py-1">
              {verConcluidas ? "Ocultar" : `Concluídas (${concluidas.length})`}
            </button>
          )}
        </div>
      </div>

      {criando && (
        <FormAtividade inicial={{ titulo: "", tipo: "ligacao", vence_em: emDias(1) }}
          onSalvar={criar} onCancelar={() => setCriando(false)} />
      )}

      {itens.length === 0 && !criando && (
        <div className="text-xs text-ink-4 mb-2">
          Nenhum próximo passo agendado.
        </div>
      )}

      <div className="space-y-1.5">
        {itens.map((a) => (
          <div key={a.id} className={`border rounded-lg px-3 py-2 ${
            a.atrasada ? "border-danger bg-danger-soft" : "border-line bg-surface"
          }`} data-testid={`atividade-${a.id}`}>
            <div className="flex items-start gap-2">
              <button onClick={() => setConcluindo(concluindo === a.id ? null : a.id)}
                title="Concluir" data-testid={`concluir-${a.id}`}
                className="w-5 h-5 border border-line-strong hover:bg-success-soft hover:border-success flex items-center justify-center shrink-0 mt-0.5 rounded-sm">
                {concluindo === a.id && <Check size={12} weight="regular" className="text-success" />}
              </button>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold leading-tight">{a.titulo}</div>
                <div className="flex items-center gap-1.5 text-[11px] mt-0.5">
                  <span className="text-ink-3"><IconeTipo tipo={a.tipo} size={11} /></span>
                  <span className={`tabular-nums ${a.atrasada ? "text-danger font-bold" : "text-ink-3"}`}>
                    {quando(a.vence_em)}{a.atrasada ? " · atrasada" : ""}
                  </span>
                  {mostrarVinculos && a.contact && (
                    <span className="text-ink-2 truncate">· {a.contact.nome}</span>
                  )}
                  {mostrarVinculos && a.deal && (
                    <span className="text-ink-2 truncate">· {a.deal.titulo}</span>
                  )}
                </div>
                {a.descricao && <div className="text-xs text-ink-2 mt-1">{a.descricao}</div>}
              </div>
              <button onClick={() => excluir(a)} className="p-0.5 hover:bg-danger-soft text-danger shrink-0">
                <Trash size={12} weight="regular" />
              </button>
            </div>

            {concluindo === a.id && (
              // O próximo passo é perguntado aqui porque é aqui que a pessoa
              // está pensando no assunto: "depois eu agendo" é como se perde a
              // venda.
              <FormAtividade titulo="Qual é o próximo passo?"
                descricao={`Concluindo: ${a.titulo}`}
                rotulo="Concluir e agendar"
                inicial={{ titulo: "", tipo: a.tipo, vence_em: emDias(3) }}
                onSalvar={(form) => concluir(a.id, form)}
                onCancelar={() => setConcluindo(null)}
                extra={(
                  <button type="button" onClick={() => concluir(a.id, null)}
                    className="text-xs text-ink-2 underline mr-auto"
                    data-testid={`concluir-sem-proxima-${a.id}`}>
                    Concluir sem agendar próximo passo
                  </button>
                )} />
            )}
          </div>
        ))}
      </div>

      {verConcluidas && (
        <div className="mt-3 space-y-1">
          {concluidas.map((a) => (
            <div key={a.id} className="flex items-center gap-2 px-3 py-1.5 border border-line bg-surface-2 rounded-lg"
              data-testid={`concluida-${a.id}`}>
              <Check size={12} weight="regular" className="text-success shrink-0" />
              <span className="text-xs text-ink-3 line-through flex-1 truncate">{a.titulo}</span>
              <span className="text-[10px] tabular-nums text-ink-4 shrink-0">
                {new Date(a.concluida_em).toLocaleDateString("pt-BR")}
              </span>
              <button onClick={() => reabrir(a.id)} title="Reabrir" className="p-0.5 hover:bg-surface-3">
                <ArrowCounterClockwise size={11} weight="regular" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
