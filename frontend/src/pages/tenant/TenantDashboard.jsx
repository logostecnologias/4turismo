import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowRight, Check, CheckCircle, Sparkle, Warning, X,
} from "@phosphor-icons/react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useAcao } from "@/hooks/useAcao";
import { formatBRL } from "@/lib/currency";
import { dataCurta, expiraEm, hora, pessoas, resumoBusca } from "@/components/agenda/comum";

/**
 * Tela "Hoje": o que precisa de alguém agora.
 *
 * Substituiu o painel de contadores por módulo (08/10/2026), que repetia o menu
 * em forma de número e não dizia o que fazer. Toda linha aqui leva a uma ação:
 * confirmar, marcar presença, assumir, concluir. O que não pede ação
 * (os números do mês) vai no fim, pequeno. A montagem e as regras de escopo
 * estão em `backend/hoje.py`; esta tela só desenha e age.
 *
 * Atualiza a cada 30s: a fila e a agenda mudam enquanto a tela está aberta, e a
 * recepção a deixa aberta o dia inteiro.
 */
const ATUALIZA_MS = 30_000;

// Mesmos rótulos de `components/agenda/comum.js` (STATUS), sem a borda.
const SITUACAO = {
  agendado: { label: "Solicitada", classe: "bg-warning-soft text-warning" },
  confirmado: { label: "Confirmada", classe: "bg-success-soft text-success" },
  concluido: { label: "Realizada", classe: "bg-surface-3 text-ink-2" },
  faltou: { label: "Não compareceu", classe: "bg-danger-soft text-danger" },
};

/** Confirmar uma solicitação avisa o turista: é a mensagem que ele espera. */
function corpoStatus(status) {
  return status === "confirmado" ? { status, avisar: true } : { status };
}

function dataPorExtenso(dia, fuso) {
  // Meio-dia UTC: o dia do calendário é o que importa, não o instante.
  const d = new Date(`${dia}T12:00:00Z`);
  const t = new Intl.DateTimeFormat("pt-BR", {
    timeZone: fuso, weekday: "long", day: "numeric", month: "long",
  }).format(d);
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function mesPorExtenso(dia) {
  const t = new Intl.DateTimeFormat("pt-BR", { month: "long", timeZone: "UTC" })
    .format(new Date(`${dia}T12:00:00Z`));
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function haQuanto(iso) {
  if (!iso) return "";
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  return h < 24 ? `há ${h} h` : `há ${Math.floor(h / 24)} d`;
}

function iniciais(nome) {
  return (nome || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]).join("").toUpperCase();
}

function Cartao({ titulo, acao, children, testid }) {
  return (
    <section className="bg-surface border border-line rounded-lg p-5 min-w-0" data-testid={testid}>
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <h2 className="text-base font-semibold text-ink">{titulo}</h2>
        {acao}
      </div>
      {children}
    </section>
  );
}

function LinkVer({ para, children }) {
  return (
    <Link to={para} className="text-sm font-medium text-brand hover:underline inline-flex items-center gap-1 shrink-0">
      {children} <ArrowRight size={13} weight="regular" />
    </Link>
  );
}

function Vazio({ children }) {
  return <p className="text-sm text-ink-3 py-4">{children}</p>;
}

// ---------------------------------------------------------------------------

function AgendaDoDia({ dados, recarregar }) {
  const { itens, truncada } = dados.agenda;
  const fuso = dados.fuso;
  const agora = Date.now();
  // O próximo horário que ainda não começou ganha destaque: é por ele que a
  // recepção se orienta.
  const proximo = itens.find((a) => new Date(a.inicio).getTime() > agora
    && (a.status === "agendado" || a.status === "confirmado"));

  const [mudar, ocupado] = useAcao(async (id, status, ok) => {
    try {
      await api.patch(`/agenda/appointments/${id}/status`, corpoStatus(status));
      toast.success(ok);
      await recarregar();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Não foi possível atualizar a reserva");
    }
  });

  return (
    <Cartao titulo="Saídas de hoje" testid="hoje-agenda"
      acao={<LinkVer para="/app/agenda">Todas as saídas</LinkVer>}>
      {itens.length === 0 ? (
        <Vazio>Nenhuma reserva para hoje.</Vazio>
      ) : (
        <ul>
          {itens.map((a) => {
            const passou = new Date(a.inicio).getTime() <= agora;
            const aberto = a.status === "agendado" || a.status === "confirmado";
            const sit = SITUACAO[a.status] || SITUACAO.agendado;
            return (
              <li key={a.id}
                className={`grid grid-cols-[3.5rem_minmax(0,1fr)] sm:grid-cols-[3.5rem_minmax(0,1fr)_auto] gap-x-4 gap-y-2 items-center py-3 border-t border-line first:border-t-0 ${
                  a.id === proximo?.id ? "-mx-3 px-3 rounded-md bg-brand-soft" : ""}`}
                data-testid={`hoje-horario-${a.id}`}>
                <time className="tabular-nums font-semibold text-ink">{hora(a.inicio, fuso)}</time>
                <div className="min-w-0">
                  <div className="font-semibold text-ink truncate">{a.contact_nome || "Cliente"}</div>
                  <div className="text-sm text-ink-3 truncate">
                    {[a.service_nome, pessoas(a)].filter(Boolean).join(" · ")}
                  </div>
                  {resumoBusca(a, fuso) && (
                    <div className="text-xs text-ink-3 truncate tabular-nums" data-testid={`hoje-busca-${a.id}`}>
                      {resumoBusca(a, fuso)}
                    </div>
                  )}
                </div>
                <div className="col-start-2 sm:col-start-auto flex flex-wrap items-center gap-2 sm:justify-end">
                  <span className={`text-xs font-medium px-2.5 py-1 rounded-full whitespace-nowrap ${sit.classe}`}>
                    {sit.label}
                  </span>
                  {aberto && !passou && a.status === "agendado" && (
                    <button type="button" disabled={ocupado} className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs"
                      onClick={() => mudar(a.id, "confirmado", "Reserva confirmada")}>
                      <Check size={13} /> Confirmar
                    </button>
                  )}
                  {aberto && passou && (
                    <>
                      <button type="button" disabled={ocupado} className="zz-btn !py-1 !px-2.5 text-xs"
                        onClick={() => mudar(a.id, "concluido", "Marcada como realizada")}>
                        <CheckCircle size={13} /> Realizada
                      </button>
                      <button type="button" disabled={ocupado} className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs"
                        onClick={() => mudar(a.id, "faltou", "Não comparecimento registrado")}>
                        <X size={13} /> Não compareceu
                      </button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {truncada && <p className="text-xs text-ink-3 mt-2">Mostrando as primeiras 200 reservas. Veja todas em Saídas.</p>}
    </Cartao>
  );
}

function Fila({ dados }) {
  const nav = useNavigate();
  const { itens, total } = dados.fila;
  const [assumir, ocupado] = useAcao(async (id) => {
    try {
      await api.post(`/atendimento/sessions/${id}/assume`);
      nav("/app/inbox");
    } catch (e) {
      toast.error(e.response?.data?.detail || "Não foi possível assumir a conversa");
    }
  });

  return (
    <Cartao titulo={total ? `Esperando atendimento (${total})` : "Esperando atendimento"} testid="hoje-fila"
      acao={<LinkVer para="/app/inbox">Atendimento</LinkVer>}>
      {itens.length === 0 ? (
        <Vazio>Ninguém esperando. A fila está em dia.</Vazio>
      ) : (
        <ul>
          {itens.map((f, i) => (
            <li key={f.id} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-3 py-3 border-t border-line first:border-t-0">
              <div className="w-10 h-10 rounded-full bg-brand-soft text-brand grid place-items-center text-sm font-semibold">
                {iniciais(f.nome)}
              </div>
              <div className="min-w-0">
                <div className="flex justify-between gap-2">
                  <span className="font-semibold text-ink truncate">{f.nome || "Cliente"}</span>
                  <span className="text-xs text-ink-3 tabular-nums shrink-0">{haQuanto(f.desde)}</span>
                </div>
                {f.previa && <p className="text-sm text-ink-2 line-clamp-2 mt-0.5">{f.previa}</p>}
                <button type="button" disabled={ocupado}
                  className={`zz-btn !py-1 !px-3 text-xs mt-2 ${i === 0 ? "" : "zz-btn-secondary"}`}
                  onClick={() => assumir(f.id)} data-testid={`hoje-assumir-${f.id}`}>
                  Assumir
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Cartao>
  );
}

function Pendencias({ dados, recarregar }) {
  // Solicitações (status `agendado`) esperando a agência. A chave manteve o
  // nome de antes, mas desde a fase 1 traz todas as solicitações futuras, não
  // só as de amanhã: por isso cada linha mostra o dia além da hora.
  const solic = dados.amanha_sem_confirmacao || { total: 0, itens: [] };
  const tarefas = dados.tarefas_atrasadas;
  const parados = dados.orcamentos_parados;

  const [confirmar, confirmando] = useAcao(async (id) => {
    try {
      const r = await api.patch(`/agenda/appointments/${id}/status`, corpoStatus("confirmado"));
      toast.success(r.data?.aviso_enviado === false
        ? "Reserva confirmada, mas o aviso não pôde ser enviado pelo WhatsApp"
        : "Reserva confirmada e cliente avisado");
      await recarregar();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Não foi possível confirmar a reserva");
    }
  });
  const [concluir, concluindo] = useAcao(async (id) => {
    try {
      await api.post(`/activities/${id}/complete`, {});
      toast.success("Tarefa concluída");
      await recarregar();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Não foi possível concluir a tarefa");
    }
  });

  const nada = !solic.total && !tarefas.total && !(parados && parados.total);

  return (
    <Cartao titulo="Pendências" testid="hoje-pendencias">
      {nada && <Vazio>Nada pendente. Tudo em dia.</Vazio>}

      {solic.total > 0 && (
        <div className="py-3 border-t border-line first:border-t-0" data-testid="hoje-solicitacoes">
          <div className="text-sm font-medium text-ink mb-1">
            {solic.total === 1 ? "1 solicitação aguardando confirmação" : `${solic.total} solicitações aguardando confirmação`}
          </div>
          <ul className="space-y-2">
            {solic.itens.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0">
                  <span className="block truncate">
                    <span className="tabular-nums text-ink-3 mr-2">
                      {dataCurta(a.inicio, dados.fuso)} {hora(a.inicio, dados.fuso)}
                    </span>
                    <span className="text-ink">{a.contact_nome || "Cliente"}</span>
                  </span>
                  <span className="block text-xs text-ink-3 truncate">
                    {[a.service_nome, pessoas(a), a.expira_em ? expiraEm(a.expira_em) : null]
                      .filter(Boolean).join(" · ")}
                  </span>
                </span>
                <button type="button" disabled={confirmando} onClick={() => confirmar(a.id)}
                  data-testid={`hoje-confirmar-${a.id}`}
                  className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs shrink-0">
                  <Check size={13} /> Confirmar
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tarefas.total > 0 && (
        <div className="py-3 border-t border-line first:border-t-0">
          <div className="flex items-baseline justify-between gap-3 mb-1">
            <span className="text-sm font-medium text-danger">
              {tarefas.total === 1 ? "1 tarefa atrasada" : `${tarefas.total} tarefas atrasadas`}
            </span>
            {dados.admin && <LinkVer para="/app/atividades">Tarefas</LinkVer>}
          </div>
          <ul className="space-y-1.5">
            {tarefas.itens.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-ink truncate min-w-0">{t.titulo}</span>
                <button type="button" disabled={concluindo} onClick={() => concluir(t.id)}
                  className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs shrink-0">
                  <Check size={13} /> Concluir
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {parados && parados.total > 0 && (
        <div className="py-3 border-t border-line first:border-t-0">
          <div className="flex items-baseline justify-between gap-3 mb-1">
            <span className="text-sm font-medium text-ink">
              {parados.total === 1 ? "1 orçamento parado" : `${parados.total} orçamentos parados`}
              <span className="text-ink-3 font-normal"> · {formatBRL(parados.valor)} sem próximo passo</span>
            </span>
            <LinkVer para="/app/funil">Orçamentos</LinkVer>
          </div>
          <ul className="space-y-1">
            {parados.itens.slice(0, 4).map((d) => (
              <li key={d.id} className="flex justify-between gap-3 text-sm">
                <span className="text-ink truncate min-w-0">{d.titulo}</span>
                <span className="tabular-nums text-ink-2 shrink-0">{formatBRL(d.valor || 0)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Cartao>
  );
}

function Alertas({ alertas }) {
  if (!alertas || !alertas.length) return null;
  const destino = { whatsapp: "/app/whatsapp", assistente: "/app/ai-agents" };
  return (
    <div className="space-y-2 mb-6" data-testid="hoje-alertas">
      {alertas.map((a, i) => (
        <div key={i} className="flex flex-wrap items-center gap-3 border border-danger bg-danger-soft text-danger rounded-lg px-4 py-3">
          <Warning size={18} className="shrink-0" />
          <span className="text-sm flex-1 min-w-[12rem]">{a.texto}</span>
          <Link to={destino[a.tipo] || "/app"} className="zz-btn zz-btn-danger !py-1.5 text-xs">Resolver</Link>
        </div>
      ))}
    </div>
  );
}

function Mes({ dados }) {
  const m = dados.mes;
  if (!m) return null;
  const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  const itens = [
    { rotulo: "Reservas realizadas", valor: m.consultas_realizadas },
    { rotulo: "Não comparecimento", valor: pct(m.taxa_falta),
      nota: m.taxa_falta == null ? "sem passeios realizados ainda" : `${m.faltas} não compareceu(ram)` },
    { rotulo: "Orçamentos aprovados", valor: formatBRL(m.orcamentos_aprovados.valor || 0),
      nota: `${m.orcamentos_aprovados.n} aprovado(s)` },
    { rotulo: "Clientes novos", valor: m.pacientes_novos },
  ];
  return (
    <section className="mt-8" data-testid="hoje-mes">
      <h2 className="text-sm font-medium text-ink-3 mb-3">{mesPorExtenso(dados.dia)} até agora</h2>
      <div className="grid grid-cols-2 lg:grid-cols-5 bg-surface border border-line rounded-lg">
        {itens.map((it, i) => (
          <div key={it.rotulo} className={`p-4 border-line ${i % 2 ? "border-l" : ""} lg:border-l ${i === 0 ? "lg:border-l-0" : ""} ${i >= 2 ? "border-t lg:border-t-0" : ""}`}>
            <div className="text-sm text-ink-3">{it.rotulo}</div>
            <div className="font-heading text-3xl tabular-nums mt-1">{it.valor}</div>
            {it.nota && <div className="text-xs text-ink-3 mt-0.5">{it.nota}</div>}
          </div>
        ))}
        <div className="p-4 border-t lg:border-t-0 lg:border-l border-line col-span-2 lg:col-span-1">
          <div className="text-sm text-ink-3 flex items-center gap-1.5"><Sparkle size={14} className="text-brand" /> Assistente de IA</div>
          <div className="font-heading text-3xl tabular-nums mt-1">
            {m.assistente.sem_recepcao}<span className="text-base text-ink-3" style={{ fontFamily: "var(--font-body)" }}> de {m.assistente.total}</span>
          </div>
          <div className="text-xs text-ink-3 mt-0.5">conversas resolvidas sem atendimento humano</div>
        </div>
      </div>
    </section>
  );
}

export default function TenantDashboard() {
  const { user } = useAuth();
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState(null);

  const carregar = useCallback(async () => {
    try {
      const r = await api.get("/dashboard/hoje");
      setDados(r.data);
      setErro(null);
    } catch (e) {
      setErro(e.response?.data?.detail || "Não foi possível carregar o dia");
    }
  }, []);

  useEffect(() => {
    carregar();
    const t = setInterval(carregar, ATUALIZA_MS);
    return () => clearInterval(t);
  }, [carregar]);

  const primeiroNome = (user?.name || "").split(" ")[0];

  if (!dados) {
    return (
      <div className="zz-page" data-testid="tenant-dashboard">
        <h1 className="font-heading text-4xl">Hoje</h1>
        <p className={`mt-2 text-sm ${erro ? "text-danger" : "text-ink-3"}`}>{erro || "Carregando…"}</p>
      </div>
    );
  }

  const ps = dados.agenda.por_status;
  const totalDia = dados.agenda.itens.length;
  const resumo = totalDia === 0
    ? "Nenhuma reserva para hoje"
    : `${totalDia} ${totalDia === 1 ? "reserva" : "reservas"} · ${(ps.confirmado || 0) + (ps.concluido || 0)} confirmada(s) · ${ps.agendado || 0} aguardando confirmação`;

  return (
    <div className="zz-page" data-testid="tenant-dashboard">
      <header className="mb-6">
        <h1 className="font-heading text-4xl">{dataPorExtenso(dados.dia, dados.fuso)}</h1>
        <p className="text-ink-2 mt-1">
          {primeiroNome ? `Bom trabalho, ${primeiroNome}. ` : ""}{resumo}.
        </p>
      </header>

      <Alertas alertas={dados.alertas} />

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-5 items-start">
        <AgendaDoDia dados={dados} recarregar={carregar} />
        <div className="grid gap-5 min-w-0">
          <Fila dados={dados} />
          <Pendencias dados={dados} recarregar={carregar} />
        </div>
      </div>

      <Mes dados={dados} />
    </div>
  );
}
