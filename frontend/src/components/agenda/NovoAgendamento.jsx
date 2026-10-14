import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { CalendarBlank, MagnifyingGlass, Minus, Plus, Warning, X } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { CampoHora, REGEX_RELOGIO } from "./CampoHora";
import { CamposEmbarque, corpoEmbarque, embarqueDe, problemaEmbarque } from "./Embarque";
import {
  hojeNaEmpresa, hora, instanteDe, moeda, pessoas as rotuloPessoas, rotuloDia, somaDias,
  totalReserva,
} from "./comum";

// Reserva manual: a agência atendendo o telefone ou o balcão.
//
// As saídas vêm da **mesma** rota que a grade da tela de Saídas e que o bot
// consulta (`GET /agenda/saidas`). Duas listas diferentes fariam a tela
// oferecer uma vaga que o bot acabou de ocupar, e o contrário, que é pior.
// Mesmo assim a vaga só é garantida no servidor: o 409 "Não há vagas
// suficientes" é o caso real de corrida, e a lista é recarregada quando ele
// chega.
//
// O cliente é escolhido por busca ou cadastrado ali mesmo, pelo número: quem
// está com o turista no telefone não vai abrir outra tela para cadastrar antes
// de reservar.

const MAX_PESSOAS = 500;

/** Contador de pessoas com botões de menos e mais. Digitar também vale. */
export function Contador({ valor, onChange, min = 0, max = MAX_PESSOAS, testid }) {
  const prender = (n) => Math.min(max, Math.max(min, Number.isFinite(n) ? n : min));
  return (
    <div className="inline-flex items-center border border-line rounded-lg overflow-hidden bg-surface">
      <button type="button" className="px-2.5 py-2 text-ink-2 hover:bg-surface-2 disabled:opacity-40"
        disabled={valor <= min} aria-label="Menos" data-testid={testid && `${testid}-menos`}
        onClick={() => onChange(prender(valor - 1))}>
        <Minus size={13} weight="regular" />
      </button>
      <input className="w-12 text-center tabular-nums bg-transparent outline-none text-sm py-2"
        inputMode="numeric" value={valor} data-testid={testid}
        onChange={(e) => onChange(prender(parseInt(e.target.value.replace(/\D/g, ""), 10) || 0))} />
      <button type="button" className="px-2.5 py-2 text-ink-2 hover:bg-surface-2 disabled:opacity-40"
        disabled={valor >= max} aria-label="Mais" data-testid={testid && `${testid}-mais`}
        onClick={() => onChange(prender(valor + 1))}>
        <Plus size={13} weight="regular" />
      </button>
    </div>
  );
}

export default function NovoAgendamento({ servicos, fuso, admin, inicial, pontos = [], onFechar, onSalvo }) {
  const [servicoId, setServicoId] = useState(inicial?.service_id || servicos[0]?.id || "");
  const [dia, setDia] = useState(() => inicial?.dia || hojeNaEmpresa(fuso));
  const [saidas, setSaidas] = useState([]);
  const [bloqueio, setBloqueio] = useState(null);
  const [carregandoSaidas, setCarregando] = useState(false);
  const [escolhida, setEscolhida] = useState(inicial?.inicio || null);
  const [adultos, setAdultos] = useState(1);
  const [criancas, setCriancas] = useState(0);
  const [busca, setBusca] = useState("");
  const [achados, setAchados] = useState([]);
  const [contato, setContato] = useState(null);
  const [novo, setNovo] = useState({ nome: "", numero: "" });
  const [observacoes, setObservacoes] = useState("");
  const [status, setStatus] = useState("confirmado");
  const [avisar, setAvisar] = useState(true);
  const [encaixe, setEncaixe] = useState(false);
  const [horaEncaixe, setHoraEncaixe] = useState("");
  const [embarque, setEmbarque] = useState(() => embarqueDe(null));
  const [erro, setErro] = useState("");

  const servico = servicos.find((s) => s.id === servicoId);
  const grupo = adultos + criancas;
  const total = totalReserva(servico, adultos, criancas);

  const buscarSaidas = useCallback(() => {
    if (!servicoId || !dia) return;
    setCarregando(true);
    api.get("/agenda/saidas", { params: { de: dia, ate: dia, service_id: servicoId } })
      .then((r) => {
        const d = (r.data.dias || [])[0] || {};
        setSaidas(d.saidas || []);
        setBloqueio(d.bloqueio || null);
      })
      .catch((e) => {
        setSaidas([]);
        setBloqueio(null);
        toast.error(formatApiErrorDetail(e.response?.data?.detail));
      })
      .finally(() => setCarregando(false));
  }, [servicoId, dia]);

  useEffect(() => { buscarSaidas(); }, [buscarSaidas]);

  useEffect(() => {
    const termo = busca.trim();
    if (termo.length < 2) { setAchados([]); return; }
    const t = setTimeout(() => {
      api.get("/contacts", { params: { q: termo, limit: 8 } })
        .then((r) => setAchados(r.data.items || [])).catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [busca]);

  // O instante que vai para o servidor: a saída escolhida na lista ou, no
  // encaixe, a hora digitada convertida no fuso da agência.
  const horaDigitada = admin && encaixe && REGEX_RELOGIO.test(horaEncaixe);
  const inicio = horaDigitada ? instanteDe(dia, horaEncaixe, fuso) : escolhida;
  const saidaEscolhida = saidas.find((s) => s.inicio === escolhida);
  // A hora da saída, para calcular a busca (saída − minutos antes do ponto).
  const horaSaida = horaDigitada ? horaEncaixe
    : (saidaEscolhida?.hora || (inicio ? hora(inicio, fuso) : ""));

  const [salvar, salvando] = useAcao(async () => {
    setErro("");
    if (!inicio) return toast.error(encaixe ? "Digite a hora do encaixe" : "Escolha uma saída");
    if (adultos < 1) return toast.error("A reserva precisa de pelo menos um adulto");
    if (!contato && !novo.numero.trim())
      return toast.error("Escolha um cliente ou informe o WhatsApp");
    const problema = problemaEmbarque(embarque, pontos, horaSaida);
    if (problema) return toast.error(problema);
    try {
      const r = await api.post("/agenda/appointments", {
        service_id: servicoId,
        inicio,
        adultos,
        criancas,
        contact_id: contato?.id || null,
        contact_nome: contato ? null : novo.nome,
        contact_numero: contato ? null : novo.numero,
        observacoes: observacoes || null,
        ...corpoEmbarque(embarque),
        status,
        forcar: !!(admin && encaixe),
        avisar: status === "confirmado" && avisar,
      });
      const avisou = status === "confirmado" && avisar;
      toast.success(
        avisou && r.data.aviso_enviado === false
          ? "Reserva feita, mas a confirmação não pôde ser enviada pelo WhatsApp"
          : status === "agendado" ? "Solicitação registrada" : "Reserva confirmada",
      );
      onSalvo();
    } catch (e) {
      // 409 é vaga que acabou entre a consulta e o clique (ou grupo maior que
      // a saída). A mensagem do servidor diz quantos lugares restam, e a lista
      // é recarregada para mostrar o número novo.
      const msg = formatApiErrorDetail(e.response?.data?.detail);
      setErro(msg);
      toast.error(msg);
      if (e.response?.status === 409) buscarSaidas();
    }
  });

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      data-testid="novo-agendamento">
      <div className="bg-surface border border-line rounded-2xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-line">
          <h2 className="font-heading text-2xl font-semibold">Nova reserva</h2>
          <button type="button" onClick={onFechar} className="zz-btn-icon"
            data-testid="fechar-novo-agendamento" aria-label="Fechar">
            <X size={18} weight="regular" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Campo label="Passeio">
              <select className="zz-input" value={servicoId} data-testid="agendamento-servico"
                onChange={(e) => { setServicoId(e.target.value); setEscolhida(null); }}>
                {servicos.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.nome}{s.preco != null ? ` · ${moeda(s.preco)}` : ""}
                  </option>
                ))}
              </select>
            </Campo>
            <Campo label="Dia">
              <input type="date" className="zz-input" value={dia} data-testid="agendamento-dia"
                onChange={(e) => { setDia(e.target.value); setEscolhida(null); }} />
            </Campo>
          </div>

          <div>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
              <span className="text-sm font-medium text-ink-2">Saídas de {rotuloDia(dia)}</span>
              <div className="flex gap-1">
                {[-1, 1].map((n) => (
                  <button key={n} type="button" className="zz-btn zz-btn-secondary text-[11px] py-1"
                    onClick={() => { setDia(somaDias(dia, n)); setEscolhida(null); }}>
                    {n < 0 ? "Dia anterior" : "Próximo dia"}
                  </button>
                ))}
              </div>
            </div>
            {carregandoSaidas ? (
              <div className="text-xs text-ink-3 py-6 text-center">Consultando as saídas…</div>
            ) : saidas.length === 0 ? (
              <div className="text-xs text-ink-2 bg-surface-2 border border-line rounded-lg p-4 flex gap-2">
                <CalendarBlank size={16} weight="regular" className="shrink-0 mt-0.5" />
                <span>
                  {bloqueio
                    ? `Dia fechado: ${typeof bloqueio === "string" ? bloqueio : bloqueio.motivo || "sem saídas"}.`
                    : `Este passeio não tem saída em ${rotuloDia(dia)}.`}
                </span>
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2"
                data-testid="agendamento-horarios">
                {saidas.map((s) => {
                  const marcada = !horaDigitada && escolhida === s.inicio;
                  const cabe = s.vagas >= grupo;
                  // No encaixe a saída fechada (antecedência, teto de dias)
                  // volta a ser escolhível: é exatamente o que `forcar` aceita.
                  // Lugar a mais, nunca.
                  const pode = cabe && (s.aberta || (admin && encaixe));
                  return (
                    <button key={s.inicio} type="button" disabled={!pode}
                      data-testid={`agendamento-saida-${s.hora}`}
                      onClick={() => { setHoraEncaixe(""); setEscolhida(s.inicio); }}
                      className={`border rounded-lg px-3 py-2 text-left transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                        marcada
                          ? "border-info bg-info-soft ring-1 ring-info"
                          : "border-line hover:border-line-strong hover:bg-surface-2"}`}>
                      <div className="tabular-nums font-bold text-sm">{s.hora || hora(s.inicio, fuso)}</div>
                      <div className={`text-[11px] tabular-nums ${s.vagas === 0 ? "text-danger" : "text-ink-3"}`}>
                        {s.vagas === 0 ? "Lotada"
                          : `${s.vagas} ${s.vagas === 1 ? "vaga" : "vagas"} de ${s.capacidade}`}
                      </div>
                      {!s.aberta && s.vagas > 0 && (
                        <div className="text-[10px] text-ink-3">fechada para reservas</div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {admin && (
            <div className="border border-line rounded-lg p-3 space-y-2">
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={encaixe} className="mt-0.5"
                  data-testid="agendamento-encaixe"
                  onChange={(e) => {
                    setEncaixe(e.target.checked);
                    // Sem encaixe, a saída fechada que estava escolhida deixa de valer.
                    if (!e.target.checked && saidaEscolhida && !saidaEscolhida.aberta) setEscolhida(null);
                  }} />
                <span>
                  Encaixe
                  <span className="block text-[11px] text-ink-3">
                    Aceita uma saída fora da grade ou já fechada pela antecedência. A capacidade
                    continua valendo: encaixe nunca passa do número de lugares.
                  </span>
                </span>
              </label>
              {encaixe && (
                <div className="flex flex-wrap items-center gap-2 pl-6">
                  <span className="text-xs text-ink-2">Saída extra às</span>
                  <CampoHora valor={horaEncaixe} onChange={setHoraEncaixe}
                    testid="agendamento-hora-encaixe" />
                  <span className="text-[11px] text-ink-3">
                    ou escolha acima uma saída fechada para reservas.
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
            <Campo label="Adultos">
              <Contador valor={adultos} min={1} onChange={setAdultos} testid="agendamento-adultos" />
            </Campo>
            <Campo label="Crianças">
              <Contador valor={criancas} min={0} onChange={setCriancas} testid="agendamento-criancas" />
            </Campo>
            <div className="text-sm" data-testid="agendamento-total">
              <div className="text-ink-3 text-xs mb-1">Total</div>
              {total == null ? (
                <span className="text-ink-3">Passeio sem preço cadastrado</span>
              ) : (
                <>
                  <div className="font-semibold tabular-nums text-lg leading-tight">{moeda(total)}</div>
                  <div className="text-[11px] text-ink-3 tabular-nums">
                    {adultos} × {moeda(servico.preco)}
                    {criancas > 0 && ` + ${criancas} × ${moeda(servico.preco_crianca ?? servico.preco)}`}
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="border-t border-line pt-5">
            <span className="text-sm font-medium text-ink-2 block mb-2">Cliente</span>
            {contato ? (
              <div className="flex items-center justify-between border border-info bg-info-soft ring-1 ring-info rounded-lg p-3">
                <div>
                  <div className="font-semibold text-sm">{contato.nome}</div>
                  <div className="text-[11px] tabular-nums text-ink-3">{contato.numero_whatsapp}</div>
                </div>
                <button type="button" className="zz-btn zz-btn-secondary text-xs" onClick={() => setContato(null)}>
                  Trocar
                </button>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="relative">
                  <MagnifyingGlass size={14} weight="regular"
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
                  <input className="zz-input pl-8" placeholder="Buscar cliente por nome ou número"
                    value={busca} data-testid="agendamento-busca-contato"
                    onChange={(e) => setBusca(e.target.value)} />
                </div>
                {achados.length > 0 && (
                  <div className="border border-line rounded-lg divide-y divide-line max-h-40 overflow-y-auto">
                    {achados.map((c) => (
                      <button key={c.id} type="button"
                        onClick={() => { setContato(c); setBusca(""); setAchados([]); }}
                        className="w-full text-left px-3 py-2 hover:bg-surface-2">
                        <div className="text-sm">{c.nome}</div>
                        <div className="text-[11px] tabular-nums text-ink-3">{c.numero_whatsapp}</div>
                      </button>
                    ))}
                  </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Campo label="Ou cadastre agora: nome">
                    <input className="zz-input" value={novo.nome} data-testid="agendamento-novo-nome"
                      onChange={(e) => setNovo({ ...novo, nome: e.target.value })} />
                  </Campo>
                  <Campo label="WhatsApp">
                    <input className="zz-input tabular-nums" placeholder="(98) 99999-8888"
                      value={novo.numero} data-testid="agendamento-novo-numero"
                      onChange={(e) => setNovo({ ...novo, numero: e.target.value })} />
                  </Campo>
                </div>
              </div>
            )}
          </div>

          <div className="border-t border-line pt-5">
            <span className="text-sm font-medium text-ink-2 block mb-2">Embarque</span>
            <CamposEmbarque valor={embarque} onChange={setEmbarque} pontos={pontos}
              horaSaida={horaSaida} testid="agendamento-embarque" />
          </div>

          <Campo label="Observações">
            <textarea rows={2} className="zz-input" value={observacoes}
              data-testid="agendamento-observacoes"
              placeholder="Restrições, pedidos, como o cliente chegou…"
              onChange={(e) => setObservacoes(e.target.value)} />
            <p className="text-[11px] text-ink-3 mt-1">Fica só na reserva: o cliente não recebe.</p>
          </Campo>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
            <Campo label="Situação da reserva">
              <select className="zz-input" value={status} data-testid="agendamento-status"
                onChange={(e) => setStatus(e.target.value)}>
                <option value="confirmado">Confirmada</option>
                <option value="agendado">Solicitada (a confirmar)</option>
              </select>
              <p className="text-[11px] text-ink-3 mt-1">
                {status === "agendado"
                  ? "Segura a vaga até alguém confirmar, ou até o prazo de confirmação em Configuração."
                  : "Já combinada com o cliente."}
              </p>
            </Campo>
            <label className={`flex items-start gap-2 text-sm sm:mt-7 ${
              status === "confirmado" ? "cursor-pointer" : "opacity-50"}`}>
              <input type="checkbox" checked={status === "confirmado" && avisar}
                disabled={status !== "confirmado"} data-testid="agendamento-avisar"
                onChange={(e) => setAvisar(e.target.checked)} className="mt-0.5" />
              <span>
                Avisar o cliente pelo WhatsApp
                <span className="block text-[11px] text-ink-3">
                  {status === "confirmado"
                    ? "Manda a confirmação com passeio, dia, hora e pessoas."
                    : "A confirmação sai quando a solicitação for confirmada."}
                </span>
              </span>
            </label>
          </div>

          {inicio && (
            <div className="text-xs bg-surface-2 border border-line rounded-lg p-3 flex gap-2">
              <Warning size={14} weight="regular" className="shrink-0 mt-0.5 text-ink-3" />
              <span>
                Vai reservar <strong>{servico?.nome}</strong> em{" "}
                <strong>{rotuloDia(dia)} às {horaDigitada ? horaEncaixe : (saidaEscolhida?.hora || hora(inicio, fuso))}</strong>{" "}
                para <strong>{rotuloPessoas({ adultos, criancas })}</strong>
                {horaDigitada || (saidaEscolhida && !saidaEscolhida.aberta) ? " (encaixe)" : ""}.
              </span>
            </div>
          )}

          {erro && (
            <div className="text-xs text-danger bg-danger-soft border border-danger rounded-lg p-3"
              data-testid="agendamento-erro">
              {erro}
            </div>
          )}
        </div>

        <div className="border-t border-line px-6 py-4 flex justify-end gap-2">
          <button type="button" className="zz-btn zz-btn-secondary" onClick={onFechar}>Cancelar</button>
          <button type="button" className="zz-btn" onClick={salvar} disabled={salvando || !inicio}
            data-testid="agendamento-salvar">
            {salvando ? "Reservando…" : "Reservar"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Campo({ label, children }) {
  return (
    <div>
      <label className="text-sm font-medium text-ink-2 block mb-1.5">
        {label}
      </label>
      {children}
    </div>
  );
}
