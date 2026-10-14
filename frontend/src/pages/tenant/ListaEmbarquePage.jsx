import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft, Printer, Copy, CheckCircle, Prohibit, MapPin, Van, IdentificationBadge,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { dataCompleta, diaDe, pessoas, telefone } from "@/components/agenda/comum";

// Lista de embarque de uma saída: o papel que o guia leva na van.
//
// É rota própria (`/app/agenda/embarque?service_id=&inicio=`), e não um modal,
// por causa da impressão: um modal vive dentro de uma caixa com altura máxima e
// rolagem, e a impressão cortaria a lista na primeira página. Aqui a página é
// a lista, e o CSS de impressão (`print:`) esconde a barra lateral, os botões e
// as cores: sai legível numa impressora preto e branco.
//
// Os grupos chegam do servidor já na ordem do roteiro (hora de busca, ponto,
// nome; a combinar no fim) e são só agrupados aqui, sem reordenar: a ordem é
// regra do servidor, e o `texto` do WhatsApp sai na mesma.

function chave(g) {
  return `${g.hora_busca || ""}|${g.ponto_embarque_nome || ""}`;
}

/** Agrupa os grupos consecutivos com o mesmo ponto e hora de busca. */
function porParada(grupos) {
  const paradas = [];
  grupos.forEach((g) => {
    const ultima = paradas[paradas.length - 1];
    if (ultima && ultima.chave === chave(g)) ultima.grupos.push(g);
    else {
      paradas.push({
        chave: chave(g),
        hora: g.hora_busca || null,
        ponto: g.ponto_embarque_nome || null,
        endereco: g.ponto_embarque_endereco || null,
        grupos: [g],
      });
    }
  });
  return paradas;
}

async function copiar(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    // Sem permissão de área de transferência (http sem TLS, navegador antigo):
    // o caminho velho do textarea ainda funciona na maioria deles.
    try {
      const t = document.createElement("textarea");
      t.value = texto;
      t.setAttribute("readonly", "");
      t.style.position = "fixed";
      t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(t);
      return ok;
    } catch {
      return false;
    }
  }
}

export default function ListaEmbarquePage() {
  const [params] = useSearchParams();
  const serviceId = params.get("service_id") || "";
  const inicio = params.get("inicio") || "";
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");

  const carregar = useCallback(() => {
    if (!serviceId || !inicio) {
      setErro("Saída não informada.");
      return;
    }
    api.get("/agenda/saidas/embarque", { params: { service_id: serviceId, inicio } })
      .then((r) => { setDados(r.data); setErro(""); })
      .catch((e) => setErro(formatApiErrorDetail(e.response?.data?.detail)));
  }, [serviceId, inicio]);

  useEffect(() => { carregar(); }, [carregar]);

  const [marcar, marcando] = useAcao(async (g, status, ok) => {
    try {
      await api.patch(`/agenda/appointments/${g.id}/status`, { status });
      toast.success(ok);
      carregar();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const fuso = dados?.fuso || "America/Sao_Paulo";
  const voltar = `/app/agenda${inicio ? `?dia=${diaDe(inicio, fuso)}` : ""}`;

  if (erro) {
    return (
      <div className="zz-page" data-testid="lista-embarque">
        <Link to={voltar} className="zz-btn zz-btn-secondary text-xs mb-4 inline-flex">
          <ArrowLeft size={14} weight="regular" /> Voltar às saídas
        </Link>
        <div className="text-sm text-danger bg-danger-soft border border-danger rounded-lg p-4">{erro}</div>
      </div>
    );
  }
  if (!dados) {
    return <div className="zz-page text-sm text-ink-3">Carregando lista de embarque…</div>;
  }

  const { saida = {}, passeio = {}, grupos = [], por_ponto: porPonto = [], totais = {} } = dados;
  const paradas = porParada(grupos);
  const jaSaiu = new Date(saida.inicio || inicio).getTime() <= Date.now();
  const resumoParada = (p) => porPonto.find(
    (x) => (x.ponto || null) === p.ponto && (x.hora_busca || null) === p.hora);

  const copiarTexto = async () => {
    if (await copiar(dados.texto || "")) toast.success("Lista copiada. É só colar no WhatsApp.");
    else toast.error("Não foi possível copiar. Selecione o texto e copie manualmente.");
  };

  return (
    <div className="zz-page zz-imprimir print:p-0 print:max-w-none" data-testid="lista-embarque">
      <div className="flex flex-wrap items-center gap-2 mb-5 print:hidden">
        <Link to={voltar} className="zz-btn zz-btn-secondary text-xs" data-testid="lista-embarque-voltar">
          <ArrowLeft size={14} weight="regular" /> Voltar às saídas
        </Link>
        <div className="flex-1" />
        <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid="lista-embarque-copiar"
          onClick={copiarTexto} disabled={!dados.texto}>
          <Copy size={14} weight="regular" /> Copiar para WhatsApp
        </button>
        <button type="button" className="zz-btn text-xs" data-testid="lista-embarque-imprimir"
          onClick={() => window.print()}>
          <Printer size={14} weight="regular" /> Imprimir
        </button>
      </div>

      <header className="mb-6 print:mb-4">
        <h1 className="font-heading text-3xl font-semibold print:text-2xl" data-testid="lista-embarque-titulo">
          {passeio.nome || saida.service_nome}
        </h1>
        <p className="text-ink-2 mt-1 print:text-black">
          Lista de embarque · {dataCompleta(saida.inicio || inicio, fuso)}
        </p>
        {saida.cancelada && (
          <p className="mt-2 text-sm font-semibold text-danger print:text-black" data-testid="lista-embarque-cancelada">
            SAÍDA CANCELADA: {saida.motivo_cancelamento || "sem motivo informado"}
          </p>
        )}
        <div className="flex flex-wrap gap-x-6 gap-y-1 mt-3 text-sm print:text-black">
          <span className="inline-flex items-center gap-1.5">
            <IdentificationBadge size={15} weight="regular" className="text-ink-3 print:hidden" />
            Guia: <strong>{saida.guia_nome || "não definido"}</strong>
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Van size={15} weight="regular" className="text-ink-3 print:hidden" />
            Veículo: <strong>{saida.veiculo_nome || "não definido"}</strong>
          </span>
        </div>
        {saida.equipe_obs && <p className="text-sm text-ink-2 mt-1 print:text-black">{saida.equipe_obs}</p>}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 mt-4 print:grid-cols-6"
          data-testid="lista-embarque-totais">
          {[
            ["Grupos", totais.grupos],
            ["Pessoas", totais.pessoas],
            ["Adultos", totais.adultos],
            ["Crianças", totais.criancas],
            ["Sem ponto", totais.sem_ponto],
            ["A confirmar", totais.solicitadas],
          ].map(([rotulo, n]) => (
            <div key={rotulo} className="border border-line rounded-lg px-3 py-2 print:border-black print:rounded-none">
              <div className="text-xs text-ink-3 print:text-black">{rotulo}</div>
              <div className="text-xl font-semibold tabular-nums">{n ?? 0}</div>
            </div>
          ))}
        </div>
      </header>

      {grupos.length === 0 ? (
        <div className="border border-dashed border-line rounded-xl p-10 text-center text-sm text-ink-3">
          Nenhuma reserva ocupando lugar nesta saída.
        </div>
      ) : (
        <div className="space-y-5">
          {paradas.map((p) => {
            const r = resumoParada(p);
            const nPessoas = r?.pessoas ?? p.grupos.reduce((t, g) => t + (Number(g.pessoas) || 0), 0);
            return (
              <section key={p.chave} className="break-inside-avoid-page" data-testid="lista-embarque-parada">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b-2 border-line-strong pb-1.5 mb-2 print:border-black">
                  <span className="tabular-nums text-xl font-semibold">{p.hora || "A combinar"}</span>
                  <span className="font-semibold inline-flex items-center gap-1">
                    <MapPin size={15} weight="regular" className="text-ink-3 print:hidden" />
                    {p.ponto || "Sem ponto de busca"}
                  </span>
                  {p.endereco && <span className="text-sm text-ink-3 print:text-black">{p.endereco}</span>}
                  <span className="ml-auto text-sm text-ink-3 tabular-nums print:text-black">
                    {p.grupos.length} {p.grupos.length === 1 ? "grupo" : "grupos"} · {nPessoas} {nPessoas === 1 ? "pessoa" : "pessoas"}
                  </span>
                </div>
                <ul className="divide-y divide-line print:divide-black">
                  {p.grupos.map((g) => (
                    <li key={g.id} className="py-2.5 grid grid-cols-[1.25rem_minmax(0,1fr)] sm:grid-cols-[1.25rem_minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 items-start break-inside-avoid"
                      data-testid="lista-embarque-grupo">
                      {/* Quadradinho para o guia marcar no papel quem embarcou. */}
                      <span className="mt-1 w-4 h-4 border-2 border-line-strong rounded-sm print:border-black print:rounded-none" aria-hidden />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-semibold">{g.contact_nome || "Cliente"}</span>
                          <span className="tabular-nums text-sm text-ink-2 print:text-black">{telefone(g.contact_numero)}</span>
                          {g.status === "agendado" && (
                            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-warning-soft text-warning print:bg-transparent print:text-black print:border print:border-black"
                              data-testid="lista-embarque-solicitada">
                              Solicitada
                            </span>
                          )}
                          {g.presenca_confirmada_em && (
                            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-success-soft text-success print:bg-transparent print:text-black print:border print:border-black"
                              data-testid="lista-embarque-presenca">
                              Presença confirmada
                            </span>
                          )}
                          {g.status === "concluido" && (
                            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-surface-3 text-ink-2 print:bg-transparent print:text-black">
                              Realizada
                            </span>
                          )}
                        </div>
                        <div className="text-sm text-ink-2 tabular-nums print:text-black">
                          {pessoas(g)}
                        </div>
                        {g.embarque_obs && (
                          <div className="text-sm print:text-black">Embarque: {g.embarque_obs}</div>
                        )}
                        {g.observacoes && (
                          <div className="text-sm text-ink-3 whitespace-pre-line print:text-black">{g.observacoes}</div>
                        )}
                      </div>
                      {jaSaiu && g.status === "confirmado" && (
                        <div className="col-start-2 sm:col-start-auto flex flex-wrap gap-1.5 print:hidden">
                          <button type="button" className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs" disabled={marcando}
                            data-testid="lista-embarque-realizada"
                            onClick={() => marcar(g, "concluido", "Marcada como realizada")}>
                            <CheckCircle size={13} weight="regular" /> Realizada
                          </button>
                          <button type="button" className="zz-btn zz-btn-secondary !py-1 !px-2.5 text-xs" disabled={marcando}
                            data-testid="lista-embarque-faltou"
                            onClick={() => marcar(g, "faltou", "Não comparecimento registrado")}>
                            <Prohibit size={13} weight="regular" /> Não compareceu
                          </button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
