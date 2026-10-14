// Formatação de data e hora da agenda.
//
// **Tudo aqui é exibido no fuso da EMPRESA, não no do navegador**: e isso é o
// oposto do que Atividades e Relatórios fazem. Naquelas telas "hoje" é o dia de
// quem está olhando; aqui "segunda das 9h às 18h" é o horário de funcionamento
// da empresa, e o mesmo horário precisa aparecer igual para a recepção, para o
// dono viajando e para o bot, que roda no servidor. Por isso todas as funções
// recebem `fuso` (vem de `GET /agenda/config`) e passam por `timeZone` no
// `Intl`: nunca por `new Date().getHours()`, que usaria o relógio da máquina.

const SEMANA = ["Domingo", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"];
export const DIAS_GRADE = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

function fmt(fuso, opcoes) {
  return new Intl.DateTimeFormat("pt-BR", { timeZone: fuso, ...opcoes });
}

export function hora(iso, fuso) {
  if (!iso) return "";
  return fmt(fuso, { hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

export function dataCurta(iso, fuso) {
  if (!iso) return "";
  return fmt(fuso, { day: "2-digit", month: "2-digit" }).format(new Date(iso));
}

export function dataCompleta(iso, fuso) {
  if (!iso) return "";
  const d = new Date(iso);
  const partes = fmt(fuso, {
    weekday: "long", day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).formatToParts(d);
  const p = Object.fromEntries(partes.map((x) => [x.type, x.value]));
  const diaSemana = (p.weekday || "").replace(/^./, (c) => c.toUpperCase()).replace("-feira", "");
  return `${diaSemana}, ${p.day}/${p.month} às ${p.hour}:${p.minute}`;
}

/** O dia (AAAA-MM-DD) em que este instante cai, no fuso da empresa.
 *
 * `toISOString().slice(0,10)` daria o dia em UTC: um atendimento das 22h em
 * São Paulo apareceria no dia seguinte. */
export function diaDe(iso, fuso) {
  const p = Object.fromEntries(
    fmt(fuso, { year: "numeric", month: "2-digit", day: "2-digit" })
      .formatToParts(new Date(iso))
      .map((x) => [x.type, x.value]),
  );
  return `${p.year}-${p.month}-${p.day}`;
}

/** Hoje, no fuso da empresa. */
export function hojeNaEmpresa(fuso) {
  return diaDe(new Date().toISOString(), fuso);
}

export function somaDias(dia, n) {
  // Meio-dia UTC para a soma nunca tropeçar numa virada de horário de verão:
  // o dia do calendário é o que importa aqui, não o instante.
  const d = new Date(`${dia}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function rotuloDia(dia) {
  const d = new Date(`${dia}T12:00:00Z`);
  return `${SEMANA[d.getUTCDay()]} ${String(d.getUTCDate()).padStart(2, "0")}/${
    String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Quanto aquele fuso está deslocado do UTC **naquele instante** (em ms). */
function deslocamento(instante, fuso) {
  const p = Object.fromEntries(
    fmt(fuso, {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).formatToParts(instante).map((x) => [x.type, x.value]),
  );
  const comoSeFosseUtc = Date.UTC(
    Number(p.year), Number(p.month) - 1, Number(p.day),
    Number(p.hour) % 24, Number(p.minute), Number(p.second),
  );
  return comoSeFosseUtc - instante.getTime();
}

/** O instante ISO do começo (ou fim) de um dia do calendário, no fuso da empresa.
 *
 * É o que a listagem manda em `de`/`ate`. O deslocamento é medido naquela data
 * específica: um número fixo de horas erraria em todo fuso com horário de
 * verão, e o fuso do navegador erraria sempre que quem abriu a tela não
 * estivesse no mesmo lugar que a empresa.
 *
 * A segunda passada existe porque o deslocamento é medido num instante que
 * ainda é um chute: no dia exato da virada do horário de verão, o chute e o
 * resultado caem em lados diferentes da mudança, e uma passada só erraria a
 * janela em uma hora. */
export function instanteDoDia(dia, fuso, fimDoDia = false) {
  const chute = new Date(`${dia}T${fimDoDia ? "23:59:59" : "00:00:00"}Z`);
  const primeira = new Date(chute.getTime() - deslocamento(chute, fuso));
  return new Date(chute.getTime() - deslocamento(primeira, fuso)).toISOString();
}

/** O instante ISO de uma hora de parede ("14:30") num dia, no fuso da empresa.
 *
 * É o encaixe da tela de reserva: a hora é digitada como a agência pensa, e o
 * backend recebe o instante. Mesma conta em duas passadas de `instanteDoDia`. */
export function instanteDe(dia, hhmm, fuso) {
  const chute = new Date(`${dia}T${hhmm}:00Z`);
  const primeira = new Date(chute.getTime() - deslocamento(chute, fuso));
  return new Date(chute.getTime() - deslocamento(primeira, fuso)).toISOString();
}

// Status gravado → rótulo na tela. Os valores gravados são os da agenda de
// horários (`agendado`, `concluido`…) e não mudaram com a fase 1: quem mudou é
// o que eles querem dizer. `agendado` é a reserva **solicitada**, que ainda
// espera a agência confirmar e pode expirar em `expira_em`.
export const STATUS = {
  agendado: { label: "Solicitada", classe: "bg-warning-soft text-warning border-warning" },
  confirmado: { label: "Confirmada", classe: "bg-success-soft text-success border-success" },
  concluido: { label: "Realizada", classe: "bg-surface-3 text-ink-2 border-line" },
  faltou: { label: "Não compareceu", classe: "bg-danger-soft text-danger border-danger" },
  cancelado: { label: "Cancelada", classe: "bg-surface-2 text-ink-3 border-line" },
};

/** Status que seguram lugar na saída (mesma regra do backend). */
export const OCUPAM = ["agendado", "confirmado", "concluido"];

export const ORIGEM = {
  manual: "Reservado pelo atendimento",
  bot: "Reservado pelo fluxo automático no WhatsApp",
  ia: "Reservado pelo assistente de IA",
};

/** "2 adultos e 1 criança", "4 pessoas", "1 pessoa".
 *
 * Sem crianças, fala em pessoas: "4 adultos" numa reserva de quatro amigos
 * soa como se alguém tivesse perguntado. */
export function pessoas(r) {
  if (!r) return "";
  const a = Number(r.adultos);
  const c = Number(r.criancas) || 0;
  if (!Number.isFinite(a) || r.adultos == null) {
    const n = Number(r.pessoas);
    return Number.isFinite(n) && r.pessoas != null ? `${n} ${n === 1 ? "pessoa" : "pessoas"}` : "";
  }
  if (!c) return `${a} ${a === 1 ? "pessoa" : "pessoas"}`;
  return `${a} ${a === 1 ? "adulto" : "adultos"} e ${c} ${c === 1 ? "criança" : "crianças"}`;
}

/** Quanto falta para uma solicitação expirar: "expira em 5h", "expira em 20 min".
 *
 * Volta "expirada" quando o prazo já passou e o backend ainda não varreu. */
export function expiraEm(iso, agora = Date.now()) {
  if (!iso) return "";
  const min = Math.round((new Date(iso).getTime() - agora) / 60000);
  if (min <= 0) return "prazo de confirmação vencido";
  if (min < 60) return `expira em ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `expira em ${h}h`;
  return `expira em ${Math.round(h / 24)} dias`;
}

/** Total de uma reserva: preço × adultos + (preço da criança ou do adulto) × crianças. */
export function totalReserva(servico, adultos, criancas) {
  if (!servico || servico.preco == null) return null;
  const pc = servico.preco_crianca ?? servico.preco;
  return Number(servico.preco) * (Number(adultos) || 0) + Number(pc) * (Number(criancas) || 0);
}

/** Número do WhatsApp para ler e discar: "5598981112233" → "(98) 98111-2233".
 *
 * Só formata o que reconhece (DDI 55 com 10 ou 11 dígitos de número); o resto
 * volta como veio. **Nunca acrescenta dígito**: o `wa_id` sem o nono dígito é
 * mostrado com oito, que é o número que de fato existe no WhatsApp. */
export function telefone(numero) {
  const d = String(numero || "").replace(/\D/g, "");
  const n = d.startsWith("55") && (d.length === 12 || d.length === 13) ? d.slice(2) : d;
  if (n.length === 11) return `(${n.slice(0, 2)}) ${n.slice(2, 7)}-${n.slice(7)}`;
  if (n.length === 10) return `(${n.slice(0, 2)}) ${n.slice(2, 6)}-${n.slice(6)}`;
  return numero || "";
}

/** Hora de busca de uma reserva, "07:30", ou `null` quando é a combinar. */
export function horaBusca(a, fuso) {
  if (!a) return null;
  if (a.hora_busca) return a.hora_busca;
  return a.horario_busca ? hora(a.horario_busca, fuso) : null;
}

/** "busca 07:30 · Pousada do Rio", "busca a combinar · Hotel X", ou "" sem embarque. */
export function resumoBusca(a, fuso) {
  if (!a) return "";
  const h = horaBusca(a, fuso);
  const ponto = a.ponto_embarque_nome;
  if (!h && !ponto) return "";
  return [h ? `busca ${h}` : "busca a combinar", ponto].filter(Boolean).join(" · ");
}

export function moeda(valor) {
  if (valor == null || valor === "") return "";
  return Number(valor).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}
