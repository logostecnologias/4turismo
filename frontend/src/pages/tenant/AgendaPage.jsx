import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  CaretLeft, CaretRight, Plus, Robot, Sparkle, User, CalendarBlank, Check, CheckCircle,
  Prohibit, XCircle, UsersThree, MapPin, PencilSimple, ListChecks, CloudRain, ArrowCounterClockwise,
  Van, IdentificationBadge, Warning,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { confirmToast } from "@/lib/confirm-toast";
import { useAuth } from "@/context/AuthContext";
import { useAcao } from "@/hooks/useAcao";
import Modal from "@/components/ui/Modal";
import NovoAgendamento, { Contador } from "@/components/agenda/NovoAgendamento";
import { Servicos, Profissionais } from "@/components/agenda/Catalogo";
import Configuracao from "@/components/agenda/Configuracao";
import {
  CamposEmbarque, corpoEmbarque, embarqueDe, problemaEmbarque,
} from "@/components/agenda/Embarque";
import {
  ORIGEM, STATUS, dataCompleta, expiraEm, hojeNaEmpresa, moeda, pessoas, resumoBusca, rotuloDia,
  somaDias, telefone,
} from "@/components/agenda/comum";

// As saídas da agência.
//
// A unidade da tela é a **saída** (o passeio X às 8h do dia Y), não a reserva:
// é como a agência pensa ("a Lagoa Azul de amanhã cedo está com 24 de 30") e é
// onde lotação e buraco aparecem. As reservas de cada saída abrem num painel.
//
// A grade vem pronta do servidor (`GET /agenda/saidas`), já com a ocupação
// somada: vaga livre é calculada da grade do passeio menos o que está
// reservado, nunca gravada. Esta tela só desenha.
//
// **Todo mundo da equipe vê as saídas inteiras.** Configurar (passeios, grade,
// feriados) é do admin, pela mesma regra do resto do CRM.

const ABAS_BASE = [["agenda", "Saídas"]];
// O id `profissionais` é o da aba de guias de antes da fase 1, e continua o
// mesmo para `?aba=profissionais` (e o `data-testid`) seguirem valendo.
const ABAS_ADMIN = [
  ["servicos", "Passeios"], ["profissionais", "Equipe e veículos"], ["config", "Configuração"],
];

// Sugestões rápidas de motivo ao cancelar uma saída inteira.
const MOTIVOS_CANCELAMENTO = ["Chuva forte", "Maré alta", "Lagoas secas", "Problema no veículo"];

// Reservas que um cancelamento de saída derruba (Solicitada e Confirmada).
const CANCELAVEIS = ["agendado", "confirmado"];

const ICONE_ORIGEM = { manual: User, bot: Robot, ia: Sparkle };

/** O passeio tem pelo menos uma saída na grade semanal? */
function temSaida(s) {
  return (s.grade_saidas || []).some((d) => (d || []).length > 0);
}

export default function AgendaPage() {
  const { user } = useAuth();
  const admin = user?.role === "tenant_admin";
  // `?aba=servicos` abre direto no cadastro de passeios: é por onde chega quem
  // ligou "reservar nas Saídas" no treino do assistente sem passeio nenhum.
  // `?aba=profissionais` abre Equipe e veículos. Aba de admin pedida por quem
  // não é admin é ignorada. `?dia=` abre a grade naquele dia (é por onde a
  // lista de embarque volta).
  const [params] = useSearchParams();
  const [aba, setAba] = useState(() => {
    const pedida = params.get("aba");
    return admin && ABAS_ADMIN.some(([id]) => id === pedida) ? pedida : "agenda";
  });
  const diaPedido = /^\d{4}-\d{2}-\d{2}$/.test(params.get("dia") || "") ? params.get("dia") : null;
  const [config, setConfig] = useState(null);
  const [servicos, setServicos] = useState([]);
  const [recursos, setRecursos] = useState([]);
  const [criando, setCriando] = useState(null);

  const carregarCatalogo = useCallback(() => {
    api.get("/agenda/config").then((r) => setConfig(r.data)).catch(() => {});
    api.get("/agenda/services").then((r) => setServicos(r.data)).catch(() => {});
    api.get("/agenda/resources").then((r) => setRecursos(r.data || [])).catch(() => {});
  }, []);

  useEffect(() => { carregarCatalogo(); }, [carregarCatalogo]);

  const abas = admin ? [...ABAS_BASE, ...ABAS_ADMIN] : ABAS_BASE;
  const ativos = servicos.filter((s) => s.ativo !== false);
  const pronto = ativos.some(temSaida);

  if (!config) {
    return <div className="zz-page text-sm text-ink-3">Carregando saídas…</div>;
  }

  return (
    <div className="zz-page" data-testid="agenda-page">
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Saídas</h1>
          <p className="text-ink-2 mt-2 text-sm">
            Cada passeio com suas saídas e quantos lugares já foram reservados, seja pelo
            atendimento, pelo fluxo automático do WhatsApp ou pelo assistente de IA.
          </p>
        </div>
        {aba === "agenda" && (
          <button type="button" className="zz-btn" data-testid="novo-agendamento-btn" disabled={!pronto}
            title={pronto ? "" : "Cadastre ao menos um passeio com saídas"}
            onClick={() => setCriando({})}>
            <Plus size={14} weight="regular" /> Nova reserva
          </button>
        )}
      </div>

      <div className="flex gap-1 border-b border-line mb-6 overflow-x-auto">
        {abas.map(([id, label]) => (
          <button key={id} type="button" onClick={() => setAba(id)} data-testid={`aba-${id}`}
            className={`px-4 py-2.5 text-sm font-semibold whitespace-nowrap border-b-2 -mb-px transition-colors ${
              aba === id
                ? "border-info text-info"
                : "border-transparent text-ink-3 hover:text-ink"}`}>
            {label}
          </button>
        ))}
      </div>

      {aba === "agenda" && (
        <Quadro config={config} servicos={ativos} pronto={pronto} admin={admin}
          recursos={recursos} diaInicial={diaPedido}
          onNovaReserva={(inicial) => setCriando(inicial || {})}
          onCadastrar={admin ? () => setAba("servicos") : null} />
      )}
      {aba === "servicos" && (
        <Servicos servicos={servicos} onMudou={carregarCatalogo} />
      )}
      {aba === "profissionais" && (
        <Profissionais recursos={recursos} onMudou={carregarCatalogo} />
      )}
      {aba === "config" && (
        <Configuracao config={config} onSalvo={carregarCatalogo} />
      )}

      {criando && (
        <NovoAgendamento servicos={ativos} fuso={config.fuso} admin={admin} inicial={criando}
          pontos={config.pontos_embarque || []}
          onFechar={() => setCriando(null)}
          onSalvo={() => { setCriando(null); window.dispatchEvent(new Event("agenda:recarregar")); }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grade de saídas
// ---------------------------------------------------------------------------

function telaEstreita() {
  try {
    return window.matchMedia("(max-width: 639px)").matches;
  } catch {
    return false;
  }
}

function textoBloqueio(b) {
  if (!b) return "";
  if (typeof b === "string") return b;
  return b.motivo || "Fechado";
}

function Quadro({ config, servicos, pronto, admin, recursos, diaInicial, onNovaReserva, onCadastrar }) {
  const fuso = config.fuso;
  const [dia, setDia] = useState(() => diaInicial || hojeNaEmpresa(fuso));
  // No celular, um dia por vez: sete dias de saídas empilhados viram uma
  // rolagem sem fim. Na tela larga, a semana.
  const [periodo, setPeriodo] = useState(() => (telaEstreita() ? "dia" : "semana"));
  const [servicoId, setServicoId] = useState("");
  const [dias, setDias] = useState([]);
  const [carregando, setCarregando] = useState(true);
  const [aberta, setAberta] = useState(null);

  const n = periodo === "dia" ? 1 : 7;

  const carregar = useCallback(() => {
    setCarregando(true);
    api.get("/agenda/saidas", {
      params: { de: dia, ate: somaDias(dia, n - 1), service_id: servicoId || undefined },
    })
      .then((r) => setDias(r.data.dias || []))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
      .finally(() => setCarregando(false));
  }, [dia, n, servicoId]);

  useEffect(() => { carregar(); }, [carregar]);
  useEffect(() => {
    // O modal de reserva vive na página, acima deste componente; o evento é o
    // que faz a grade recarregar sem subir o estado dela inteiro para lá.
    const h = () => carregar();
    window.addEventListener("agenda:recarregar", h);
    return () => window.removeEventListener("agenda:recarregar", h);
  }, [carregar]);

  if (!pronto) {
    return (
      <div className="border border-dashed border-line rounded-xl p-10 text-center" data-testid="saidas-vazias">
        <CalendarBlank size={28} weight="regular" className="mx-auto text-ink-3 mb-3" />
        <div className="font-semibold mb-1">As saídas ainda não estão prontas</div>
        <p className="text-sm text-ink-2 max-w-lg mx-auto">
          Falta cadastrar ao menos um passeio ativo com horários de saída. Sem isso não há
          vaga para oferecer, nem no atendimento, nem no WhatsApp.
        </p>
        {onCadastrar && (
          <button type="button" className="zz-btn mt-4" onClick={onCadastrar}
            data-testid="saidas-cadastrar-passeio">
            Cadastrar passeio
          </button>
        )}
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-5">
        <div className="flex items-center gap-1">
          <button type="button" className="zz-btn-icon" data-testid="agenda-anterior" aria-label="Anterior"
            onClick={() => setDia(somaDias(dia, -n))}>
            <CaretLeft size={16} weight="regular" />
          </button>
          <button type="button" className="zz-btn zz-btn-secondary text-xs"
            onClick={() => setDia(hojeNaEmpresa(fuso))}>Hoje</button>
          <button type="button" className="zz-btn-icon" data-testid="agenda-proximo" aria-label="Próximo"
            onClick={() => setDia(somaDias(dia, n))}>
            <CaretRight size={16} weight="regular" />
          </button>
        </div>
        <div className="font-heading text-lg font-semibold tracking-tight">
          {periodo === "dia" ? rotuloDia(dia) : `${rotuloDia(dia)} a ${rotuloDia(somaDias(dia, 6))}`}
        </div>
        <div className="flex-1" />
        <select className="zz-input max-w-[150px]" value={periodo}
          onChange={(e) => setPeriodo(e.target.value)} data-testid="agenda-periodo">
          <option value="dia">Dia</option>
          <option value="semana">7 dias</option>
        </select>
        <select className="zz-input max-w-[240px]" value={servicoId}
          onChange={(e) => setServicoId(e.target.value)} data-testid="agenda-filtro-servico">
          <option value="">Todos os passeios</option>
          {servicos.map((s) => <option key={s.id} value={s.id}>{s.nome}</option>)}
        </select>
      </div>

      {carregando && dias.length === 0 ? (
        <div className="text-sm text-ink-3 py-10 text-center">Carregando…</div>
      ) : (
        <div className="space-y-3" data-testid="grade-saidas">
          {dias.map((d) => (
            <div key={d.dia}
              className="grid grid-cols-1 sm:grid-cols-[150px_minmax(0,1fr)] gap-3 border border-line rounded-xl p-4 bg-surface"
              data-testid={`dia-${d.dia}`}>
              <div>
                <div className="font-semibold text-sm">{d.rotulo || rotuloDia(d.dia)}</div>
                {!d.bloqueio && (
                  <div className="text-[11px] text-ink-3">
                    {d.saidas.length === 0 ? "sem saídas"
                      : `${d.saidas.length} ${d.saidas.length === 1 ? "saída" : "saídas"}`}
                  </div>
                )}
              </div>
              {d.bloqueio ? (
                <div className="text-sm text-warning bg-warning-soft border border-warning rounded-lg px-3 py-2 self-start"
                  data-testid="dia-bloqueio">
                  Fechado: {textoBloqueio(d.bloqueio)}
                </div>
              ) : d.saidas.length === 0 ? (
                <div className="text-sm text-ink-3 self-center">Nenhuma saída neste dia.</div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-2">
                  {d.saidas.map((s) => (
                    <CartaoSaida key={`${s.service_id}-${s.inicio}`} s={s}
                      onAbrir={() => setAberta({ ...s, dia: d.dia })} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {aberta && (
        <PainelSaida saida={aberta} fuso={fuso} admin={admin} recursos={recursos}
          pontos={config.pontos_embarque || []}
          onFechar={() => setAberta(null)}
          onMudou={carregar}
          onNovaReserva={() => {
            const s = aberta;
            setAberta(null);
            onNovaReserva({ service_id: s.service_id, dia: s.dia, inicio: s.inicio });
          }} />
      )}
    </div>
  );
}

/** Barra de ocupação: marca, aviso perto de lotar, perigo quando lota. */
function BarraOcupacao({ reservados, capacidade }) {
  const pct = capacidade > 0 ? Math.min(100, Math.round((reservados / capacidade) * 100)) : 0;
  const cor = reservados >= capacidade ? "bg-danger" : pct >= 80 ? "bg-warning" : "bg-brand";
  return (
    <div className="h-1.5 rounded-full bg-surface-3 overflow-hidden" role="progressbar"
      aria-valuenow={reservados} aria-valuemin={0} aria-valuemax={capacidade}>
      <div className={`h-full rounded-full ${cor}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** "Ana · Van 15" — guia e veículo escalados, ou "" sem nenhum. */
function resumoEquipe(s) {
  return [s.guia_nome, s.veiculo_nome].filter(Boolean).join(" · ");
}

function CartaoSaida({ s, onAbrir }) {
  // Saída cancelada continua na grade (é onde se descobre que a chuva levou
  // a das 8h), riscada e com o motivo, e não conta como lotada.
  if (s.cancelada) {
    return (
      <button type="button" onClick={onAbrir} data-testid="cartao-saida"
        className="text-left border border-dashed border-line rounded-lg p-3 transition-colors hover:bg-surface-2 opacity-70">
        <div className="flex items-baseline justify-between gap-2">
          <span className="tabular-nums font-semibold text-lg leading-none line-through text-ink-3">{s.hora}</span>
          <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-danger-soft text-danger"
            data-testid="saida-cancelada-selo">
            Cancelada
          </span>
        </div>
        <div className="text-sm text-ink-3 truncate mt-1 line-through">{s.service_nome}</div>
        <div className="text-[11px] text-ink-2 mt-2 line-clamp-2" data-testid="saida-cancelada-motivo">
          {s.motivo_cancelamento || "Sem motivo informado"}
        </div>
      </button>
    );
  }
  const lotada = s.vagas <= 0;
  const equipe = resumoEquipe(s);
  return (
    <button type="button" onClick={onAbrir} data-testid="cartao-saida"
      className={`text-left border rounded-lg p-3 transition-colors hover:bg-surface-2 ${
        lotada ? "border-danger" : "border-line hover:border-line-strong"} ${
        s.aberta ? "" : "opacity-60"}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="tabular-nums font-semibold text-lg leading-none">{s.hora}</span>
        <span className={`tabular-nums text-sm font-semibold ${lotada ? "text-danger" : "text-ink"}`}>
          {s.reservados}/{s.capacidade}
        </span>
      </div>
      <div className="text-sm text-ink-2 truncate mt-1">{s.service_nome}</div>
      <div className="mt-2"><BarraOcupacao reservados={s.reservados} capacidade={s.capacidade} /></div>
      <div className="flex flex-wrap gap-x-2 text-[11px] mt-1.5">
        <span className={lotada ? "text-danger font-semibold" : "text-ink-3"}>
          {lotada ? "Lotada" : `${s.vagas} ${s.vagas === 1 ? "vaga" : "vagas"}`}
        </span>
        {!s.aberta && <span className="text-ink-3">· fechada para reservas</span>}
        {s.extra && <span className="text-ink-3">· encaixe</span>}
      </div>
      {equipe && (
        <div className="text-[11px] text-ink-3 truncate mt-1" data-testid="cartao-saida-equipe" title={equipe}>
          {equipe}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Painel de uma saída: as reservas dela
// ---------------------------------------------------------------------------

function PainelSaida({
  saida, fuso, admin, recursos, pontos, onFechar, onMudou, onNovaReserva,
}) {
  const navigate = useNavigate();
  const [dados, setDados] = useState(null);
  const [editandoPessoas, setEditandoPessoas] = useState(null);
  const [editandoEmbarque, setEditandoEmbarque] = useState(null);
  const [cancelandoSaida, setCancelandoSaida] = useState(false);

  const carregar = useCallback(() => {
    api.get("/agenda/saidas/reservas", { params: { service_id: saida.service_id, inicio: saida.inicio } })
      .then((r) => setDados(r.data))
      .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)));
  }, [saida.service_id, saida.inicio]);

  useEffect(() => { carregar(); }, [carregar]);

  const depois = () => { carregar(); onMudou(); };

  const [mudarStatus, mudando] = useAcao(async (a, status, ok) => {
    try {
      const corpo = status === "confirmado" ? { status, avisar: true } : { status };
      const r = await api.patch(`/agenda/appointments/${a.id}/status`, corpo);
      toast.success(status === "confirmado" && r.data?.aviso_enviado === false
        ? "Reserva confirmada, mas o aviso não pôde ser enviado pelo WhatsApp" : ok);
      depois();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const [cancelar, cancelando] = useAcao(async (a) => {
    if (!(await confirmToast(
      `Cancelar a reserva de ${a.contact_nome || "cliente"} (${pessoas(a)})?`,
      { confirmLabel: "Cancelar reserva" }))) return;
    try {
      await api.post(`/agenda/appointments/${a.id}/cancel`, null, { params: { avisar: true } });
      toast.success("Reserva cancelada e cliente avisado");
      depois();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const [salvarPessoas, salvandoPessoas] = useAcao(async () => {
    const { id, adultos, criancas } = editandoPessoas;
    try {
      await api.patch(`/agenda/appointments/${id}/pessoas`, { adultos, criancas });
      toast.success("Grupo atualizado");
      setEditandoPessoas(null);
      depois();
    } catch (e) {
      // 409: o grupo novo não cabe. A mensagem diz quantos lugares restam.
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const s = dados?.saida || saida;
  const reservas = dados?.reservas || [];

  const [salvarEmbarque, salvandoEmbarque] = useAcao(async () => {
    const { id, valor } = editandoEmbarque;
    const problema = problemaEmbarque(valor, pontos, s.hora);
    if (problema) {
      toast.error(problema);
      return;
    }
    try {
      await api.patch(`/agenda/appointments/${id}/embarque`, corpoEmbarque(valor));
      toast.success("Embarque atualizado");
      setEditandoEmbarque(null);
      depois();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const [reabrir, reabrindo] = useAcao(async () => {
    const canceladas = reservas.filter((a) => a.status === "cancelado").length;
    if (!(await confirmToast(
      `Reabrir a saída? Ela volta a aceitar reservas.${canceladas
        ? ` As ${canceladas} reserva(s) canceladas continuam canceladas: quem quiser ir precisa reservar de novo.`
        : ""}`,
      { confirmLabel: "Reabrir saída", danger: false }))) return;
    try {
      await api.post("/agenda/saidas/reabrir", { service_id: saida.service_id, inicio: saida.inicio });
      toast.success("Saída reaberta");
      depois();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const ocupado =mudando || cancelando || salvandoPessoas || salvandoEmbarque || reabrindo;

  const abrirLista = () => {
    const q = new URLSearchParams({ service_id: saida.service_id, inicio: saida.inicio });
    navigate(`/app/agenda/embarque?${q.toString()}`);
  };

  return (
    <Modal largo testId="painel-saida"
      titulo={`${s.service_nome || saida.service_nome} · ${s.hora || saida.hora}`}
      descricao={dataCompleta(s.inicio || saida.inicio, fuso)}
      onFechar={onFechar}
      rodape={
        <button type="button" className="zz-btn mr-auto" data-testid="saida-nova-reserva"
          disabled={s.cancelada || s.vagas <= 0} onClick={onNovaReserva}>
          <Plus size={14} weight="regular" /> Nova reserva nesta saída
        </button>
      }>
      {s.cancelada && (
        <div className="text-sm bg-danger-soft border border-danger rounded-lg p-3 text-danger"
          data-testid="saida-cancelada-aviso">
          <div className="font-semibold">Saída cancelada: {s.motivo_cancelamento || "sem motivo informado"}</div>
          <p className="text-xs mt-1 text-ink-2">
            Não é oferecida no WhatsApp nem aceita reserva.
            {admin && " Reabrir faz a saída voltar a aceitar reservas, mas as reservas canceladas com ela não voltam."}
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2" data-testid="saida-acoes">
        <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid="saida-lista-embarque"
          onClick={abrirLista}>
          <ListChecks size={14} weight="regular" /> Lista de embarque
        </button>
        {admin && !s.cancelada && (
          <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid="saida-cancelar"
            disabled={!dados || ocupado} onClick={() => setCancelandoSaida(true)}>
            <CloudRain size={14} weight="regular" /> Cancelar saída
          </button>
        )}
        {admin && s.cancelada && (
          <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid="saida-reabrir"
            disabled={!dados || ocupado} onClick={reabrir}>
            <ArrowCounterClockwise size={14} weight="regular" /> Reabrir saída
          </button>
        )}
      </div>

      {!s.cancelada && (
        <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_auto] gap-3 items-center">
          <div>
            <BarraOcupacao reservados={s.reservados} capacidade={s.capacidade} />
            <div className="text-xs text-ink-3 mt-1.5 tabular-nums">
              {s.reservados} de {s.capacidade} lugares reservados
              {" · "}
              {s.vagas <= 0 ? <span className="text-danger font-semibold">lotada</span>
                : `${s.vagas} ${s.vagas === 1 ? "vaga" : "vagas"}`}
              {!s.aberta && " · fechada para reservas"}
              {s.extra && " · saída de encaixe"}
            </div>
          </div>
        </div>
      )}

      {dados && (
        <EquipeSaida saida={s} recursos={recursos} onSalvo={depois} />
      )}

      {!dados ? (
        <div className="text-sm text-ink-3 py-6 text-center">Carregando reservas…</div>
      ) : reservas.length === 0 ? (
        <div className="text-sm text-ink-3 border border-dashed border-line rounded-lg p-6 text-center">
          Nenhuma reserva nesta saída ainda.
        </div>
      ) : (
        <ul className="space-y-2" data-testid="saida-reservas">
          {reservas.map((a) => (
            <Reserva key={a.id} a={a} ocupado={ocupado} fuso={fuso} pontos={pontos}
              horaSaida={s.hora}
              embarque={editandoEmbarque?.id === a.id ? editandoEmbarque.valor : null}
              onEditarEmbarque={() => setEditandoEmbarque({ id: a.id, valor: embarqueDe(a, pontos, fuso) })}
              onMudarEmbarque={(valor) => setEditandoEmbarque({ id: a.id, valor })}
              onSalvarEmbarque={salvarEmbarque}
              onCancelarEmbarque={() => setEditandoEmbarque(null)}
              editando={editandoPessoas?.id === a.id ? editandoPessoas : null}
              onEditarPessoas={() => setEditandoPessoas({
                id: a.id, adultos: a.adultos ?? a.pessoas ?? 1, criancas: a.criancas ?? 0,
              })}
              onMudarPessoas={setEditandoPessoas}
              onSalvarPessoas={salvarPessoas}
              onCancelarEdicao={() => setEditandoPessoas(null)}
              onStatus={(st, ok) => mudarStatus(a, st, ok)}
              onCancelar={() => cancelar(a)} />
          ))}
        </ul>
      )}

      {cancelandoSaida && (
        <CancelarSaida saida={s} reservas={reservas}
          onFechar={() => setCancelandoSaida(false)}
          onFeito={() => { setCancelandoSaida(false); depois(); }} />
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Equipe da saída: guia, veículo e observação
// ---------------------------------------------------------------------------
//
// Funciona em saída ainda sem reserva (o servidor cria o registro da saída se
// preciso). Quem atende também escala: é operação do dia, não configuração.

function opcoesEquipe(recursos, tipo, atualId, atualNome) {
  const ativos = (recursos || []).filter((r) => (r.tipo || "pessoa") === tipo && r.ativo !== false);
  if (atualId && !ativos.some((r) => r.id === atualId)) {
    // Escalado e depois desativado (ou apagado): continua visível enquanto
    // for o escolhido, senão abrir o painel pareceria ter tirado a pessoa.
    const r = (recursos || []).find((x) => x.id === atualId);
    return [...ativos, { id: atualId, nome: `${r?.nome || atualNome || "?"} (desativado)` }];
  }
  return ativos;
}

function EquipeSaida({ saida, recursos, onSalvo }) {
  const inicial = useMemo(() => ({
    guia_id: saida.guia_id || "",
    veiculo_id: saida.veiculo_id || "",
    observacoes: saida.equipe_obs || "",
  }), [saida.guia_id, saida.veiculo_id, saida.equipe_obs]);
  const [form, setForm] = useState(inicial);
  useEffect(() => setForm(inicial), [inicial]);

  const guias = opcoesEquipe(recursos, "pessoa", saida.guia_id, saida.guia_nome);
  const veiculos = opcoesEquipe(recursos, "sala", saida.veiculo_id, saida.veiculo_nome);
  const veiculo = (recursos || []).find((r) => r.id === form.veiculo_id);
  const mudou = form.guia_id !== inicial.guia_id || form.veiculo_id !== inicial.veiculo_id
    || form.observacoes !== inicial.observacoes;
  const naoCabe = veiculo?.capacidade && saida.reservados > veiculo.capacidade;

  const [salvar, salvando] = useAcao(async () => {
    try {
      await api.patch("/agenda/saidas/equipe", {
        service_id: saida.service_id,
        inicio: saida.inicio,
        guia_id: form.guia_id || null,
        veiculo_id: form.veiculo_id || null,
        observacoes: form.observacoes.trim() || null,
      });
      toast.success("Equipe da saída salva");
      onSalvo();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  return (
    <section className="border border-line rounded-xl p-4 space-y-3" data-testid="saida-equipe">
      <div className="text-sm font-semibold">Equipe da saída</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-ink-3 mb-1 flex items-center gap-1">
            <IdentificationBadge size={13} weight="regular" /> Guia
          </span>
          <select className="zz-input" value={form.guia_id} data-testid="saida-equipe-guia"
            onChange={(e) => setForm({ ...form, guia_id: e.target.value })}>
            <option value="">Sem guia definido</option>
            {guias.map((r) => <option key={r.id} value={r.id}>{r.nome}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-ink-3 mb-1 flex items-center gap-1">
            <Van size={13} weight="regular" /> Veículo
          </span>
          <select className="zz-input" value={form.veiculo_id} data-testid="saida-equipe-veiculo"
            onChange={(e) => setForm({ ...form, veiculo_id: e.target.value })}>
            <option value="">Sem veículo definido</option>
            {veiculos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.nome}{r.capacidade ? ` (${r.capacidade} lugares)` : ""}
              </option>
            ))}
          </select>
        </label>
      </div>
      {naoCabe && (
        <p className="text-xs text-warning flex items-center gap-1" data-testid="saida-equipe-nao-cabe">
          <Warning size={13} weight="regular" />
          {saida.reservados} pessoas reservadas e o veículo tem {veiculo.capacidade} lugares.
        </p>
      )}
      <div className="flex flex-col sm:flex-row gap-2">
        <input className="zz-input flex-1" value={form.observacoes} data-testid="saida-equipe-obs"
          placeholder="Observação para a equipe: sai do píer, levar coletes…"
          onChange={(e) => setForm({ ...form, observacoes: e.target.value })} />
        <button type="button" className="zz-btn shrink-0" data-testid="saida-equipe-salvar"
          disabled={!mudou || salvando} onClick={salvar}>
          {salvando ? "Salvando…" : "Salvar equipe"}
        </button>
      </div>
      {(recursos || []).length === 0 && (
        <p className="text-[11px] text-ink-3">
          Guias e veículos são cadastrados pelo admin em Equipe e veículos.
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Cancelar a saída inteira (chuva, maré, lagoa seca)
// ---------------------------------------------------------------------------

function CancelarSaida({ saida, reservas, onFechar, onFeito }) {
  const [motivo, setMotivo] = useState("");
  const [avisar, setAvisar] = useState(true);
  const afetadas = reservas.filter((a) => CANCELAVEIS.includes(a.status));
  const nPessoas = afetadas.reduce((t, a) => t + (Number(a.pessoas) || 0), 0);

  const confirmar = async () => {
    const m = motivo.trim();
    if (!m) {
      toast.error("Informe o motivo do cancelamento");
      return;
    }
    try {
      const r = await api.post("/agenda/saidas/cancelar", {
        service_id: saida.service_id, inicio: saida.inicio, motivo: m.slice(0, 200), avisar,
      });
      const { canceladas = 0, avisando = 0 } = r.data || {};
      const texto = `Saída cancelada. ${canceladas} ${canceladas === 1 ? "reserva cancelada" : "reservas canceladas"}.`;
      if (avisando) {
        // Os avisos saem espaçados (proteção do número contra bloqueio), depois
        // desta resposta. Quem o WhatsApp não alcançar aparece na tela Hoje.
        toast.success(`${texto} ${avisando === 1 ? "O grupo vai ser avisado" : `Os ${avisando} grupos vão ser avisados`} pelo WhatsApp nos próximos minutos. Se algum não puder ser avisado, ele aparece na tela Hoje para você ligar.`,
          { duration: 12000 });
      } else {
        toast.success(texto);
      }
      onFeito();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  return (
    <Modal testId="cancelar-saida" titulo="Cancelar saída"
      descricao={`${saida.service_nome} · ${saida.hora}`}
      onFechar={onFechar} onSalvar={confirmar}
      salvarLabel="Cancelar saída" salvandoLabel="Cancelando…" cancelarLabel="Voltar">
      <div className="text-sm bg-warning-soft border border-warning rounded-lg p-3" data-testid="cancelar-saida-afetados">
        {afetadas.length === 0
          ? "Nenhuma reserva ativa nesta saída: ela só deixa de ser oferecida."
          : <>
            <strong>{afetadas.length} {afetadas.length === 1 ? "grupo" : "grupos"}</strong>
            {" "}({nPessoas} {nPessoas === 1 ? "pessoa" : "pessoas"}) {afetadas.length === 1 ? "terá a reserva cancelada" : "terão a reserva cancelada"}.
            A saída deixa de ser oferecida.
          </>}
      </div>
      <div>
        <label className="text-sm font-medium text-ink-2 block mb-1.5" htmlFor="cancelar-saida-motivo">
          Motivo
        </label>
        <input id="cancelar-saida-motivo" className="zz-input" value={motivo} required maxLength={200}
          data-testid="cancelar-saida-motivo" placeholder="O que aconteceu?"
          onChange={(e) => setMotivo(e.target.value)} />
        <div className="flex flex-wrap gap-1.5 mt-2">
          {MOTIVOS_CANCELAMENTO.map((m) => (
            <button key={m} type="button" data-testid={`cancelar-saida-sugestao-${m}`}
              className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                motivo === m ? "border-info bg-info-soft text-info" : "border-line text-ink-2 hover:bg-surface-2"}`}
              onClick={() => setMotivo(m)}>
              {m}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-ink-3 mt-1">
          Vai para as reservas canceladas e, com o aviso marcado, para a mensagem ao turista.
        </p>
      </div>
      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input type="checkbox" checked={avisar} className="mt-0.5" data-testid="cancelar-saida-avisar"
          onChange={(e) => setAvisar(e.target.checked)} />
        <span>
          Avisar os turistas pelo WhatsApp
          <span className="block text-[11px] text-ink-3">
            Cada grupo recebe a mensagem de saída cancelada, com o motivo. Quem não escreveu nas
            últimas 24h só recebe se houver modelo aprovado em Configuração.
          </span>
        </span>
      </label>
    </Modal>
  );
}

function Reserva({
  a, ocupado, fuso, pontos, horaSaida, embarque, onEditarEmbarque, onMudarEmbarque, onSalvarEmbarque,
  onCancelarEmbarque, editando, onEditarPessoas, onMudarPessoas, onSalvarPessoas, onCancelarEdicao,
  onStatus, onCancelar,
}) {
  const st = STATUS[a.status] || STATUS.agendado;
  const IconeOrigem = ICONE_ORIGEM[a.origem] || User;
  const encerrada = ["cancelado", "concluido", "faltou"].includes(a.status);

  return (
    <li className={`border border-line rounded-xl p-4 bg-surface ${
      a.status === "cancelado" || a.status === "faltou" ? "opacity-60" : ""}`}
      data-testid="cartao-agendamento">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-sm truncate">{a.contact_nome || "Cliente"}</div>
          <div className="text-[11px] tabular-nums text-ink-3">{telefone(a.contact_numero)}</div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {a.status === "agendado" && a.expira_em && (
            <span className="text-[11px] text-warning" data-testid="reserva-expira">{expiraEm(a.expira_em)}</span>
          )}
          <span className={`text-xs font-medium px-2 py-1 rounded-full border shrink-0 ${st.classe}`}>
            {st.label}
          </span>
        </div>
      </div>

      <div className="text-sm text-ink-2 mt-2 flex flex-wrap gap-x-3 gap-y-1 items-center">
        <span className="inline-flex items-center gap-1">
          <UsersThree size={14} weight="regular" className="text-ink-3" /> {pessoas(a)}
        </span>
        {a.preco != null && <span className="tabular-nums">{moeda(a.preco)}</span>}
      </div>
      {(resumoBusca(a, fuso) || a.embarque_obs) && (
        <div className="text-xs text-ink-2 mt-1.5 flex items-start gap-1" data-testid="reserva-embarque">
          <MapPin size={13} weight="regular" className="text-ink-3 shrink-0 mt-px" />
          <span>
            {resumoBusca(a, fuso) || "busca não informada"}
            {a.ponto_embarque_endereco && <span className="text-ink-3"> · {a.ponto_embarque_endereco}</span>}
            {a.embarque_obs && <span className="block text-ink-3">{a.embarque_obs}</span>}
          </span>
        </div>
      )}
      {a.observacoes && <p className="text-xs text-ink-3 mt-1.5 whitespace-pre-line">{a.observacoes}</p>}
      {a.status === "cancelado" && a.motivo_cancelamento && (
        <p className="text-xs text-ink-3 mt-1.5">Motivo: {a.motivo_cancelamento}</p>
      )}

      {embarque && (
        <div className="mt-3 pt-3 border-t border-line space-y-3" data-testid="reserva-editar-embarque">
          <CamposEmbarque valor={embarque} onChange={onMudarEmbarque} pontos={pontos}
            horaSaida={horaSaida} testid="reserva-embarque-campos" />
          <div className="flex gap-2 justify-end">
            <button type="button" className="zz-btn zz-btn-secondary text-xs" onClick={onCancelarEmbarque}>
              Desistir
            </button>
            <button type="button" className="zz-btn text-xs" disabled={ocupado} onClick={onSalvarEmbarque}
              data-testid="reserva-salvar-embarque">
              Salvar embarque
            </button>
          </div>
        </div>
      )}

      {editando && (
        <div className="mt-3 pt-3 border-t border-line flex flex-wrap items-end gap-3" data-testid="reserva-editar-pessoas">
          <div>
            <div className="text-xs text-ink-3 mb-1">Adultos</div>
            <Contador valor={editando.adultos} min={1}
              onChange={(v) => onMudarPessoas({ ...editando, adultos: v })} testid="reserva-adultos" />
          </div>
          <div>
            <div className="text-xs text-ink-3 mb-1">Crianças</div>
            <Contador valor={editando.criancas} min={0}
              onChange={(v) => onMudarPessoas({ ...editando, criancas: v })} testid="reserva-criancas" />
          </div>
          <div className="flex gap-2">
            <button type="button" className="zz-btn zz-btn-secondary text-xs" onClick={onCancelarEdicao}>
              Desistir
            </button>
            <button type="button" className="zz-btn text-xs" disabled={ocupado} onClick={onSalvarPessoas}
              data-testid="reserva-salvar-pessoas">
              Salvar grupo
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 mt-3 pt-3 border-t border-line">
        <span className="text-[11px] text-ink-3 flex items-center gap-1" title={ORIGEM[a.origem] || a.origem}>
          <IconeOrigem size={12} weight="regular" /> {ORIGEM[a.origem] || a.origem}
        </span>
        {!encerrada && (
          <div className="flex flex-wrap gap-1.5">
            {a.status === "agendado" && (
              <button type="button" className="zz-btn !py-1 !px-2.5 text-xs" disabled={ocupado}
                data-testid="agendamento-confirmar"
                onClick={() => onStatus("confirmado", "Reserva confirmada")}>
                <Check size={13} weight="regular" /> Confirmar
              </button>
            )}
            {a.status === "confirmado" && (
              <>
                <button type="button" className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs" disabled={ocupado}
                  data-testid="agendamento-concluir"
                  onClick={() => onStatus("concluido", "Marcada como realizada")}>
                  <CheckCircle size={13} weight="regular" /> Realizada
                </button>
                <button type="button" className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs" disabled={ocupado}
                  data-testid="agendamento-faltou"
                  onClick={() => onStatus("faltou", "Não comparecimento registrado")}>
                  <Prohibit size={13} weight="regular" /> Não compareceu
                </button>
              </>
            )}
            {!editando && (
              <button type="button" className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs" disabled={ocupado}
                data-testid="agendamento-pessoas" onClick={onEditarPessoas}>
                <UsersThree size={13} weight="regular" /> Alterar pessoas
              </button>
            )}
            {!embarque && (
              <button type="button" className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs" disabled={ocupado}
                data-testid="agendamento-embarque" onClick={onEditarEmbarque}>
                <PencilSimple size={13} weight="regular" /> Editar embarque
              </button>
            )}
            <button type="button" className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs" disabled={ocupado}
              data-testid="agendamento-cancelar" onClick={onCancelar}>
              <XCircle size={13} weight="regular" /> Cancelar
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
