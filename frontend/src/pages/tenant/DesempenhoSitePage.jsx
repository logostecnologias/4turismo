import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  Warning, Compass, FileText, CursorClick, Megaphone, Translate, DeviceMobile, LinkSimple, Copy,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { formatBRL } from "@/lib/currency";
import SerieDiaria from "@/components/reports/SerieDiaria";

/**
 * Desempenho do site (`backend/site_metricas.py`): visitas, cliques internos e
 * o funil que só nós conseguimos fechar, do visitante até a reserva, por
 * origem. O Google Analytics para no clique; aqui a conversa e a reserva estão
 * no mesmo banco.
 */

const JANELAS = [["7", "7 dias"], ["30", "30 dias"], ["90", "90 dias"]];

// Os nomes dos cliques internos (`site_metricas.ALVOS`).
const ALVOS = {
  ver_passeios: "Ver passeios (topo)",
  passeio: "Cartão de passeio",
  guia: "Cartão de guia",
  escolher_data: "Escolher data (celular)",
  faq: "Pergunta frequente aberta",
  avaliacoes: "Link das avaliações",
  mapa: "Ver no mapa",
  idioma: "Troca de idioma",
  instagram: "Instagram",
  ligacao: "Ligação",
  email: "E-mail",
  link_externo: "Link para outro site",
};

const IDIOMAS = { pt: "Português", en: "Inglês", es: "Espanhol" };
const APARELHOS = { celular: "Celular", computador: "Computador", tablet: "Tablet" };

const FONTES_LINK = [
  ["instagram", "Instagram"], ["facebook", "Facebook"], ["google", "Google"],
  ["whatsapp", "WhatsApp"], ["email", "E-mail"], ["tiktok", "TikTok"],
];

const n = (v) => Number(v || 0).toLocaleString("pt-BR");

function porcento(parte, todo) {
  if (!todo) return null;
  const p = (parte / todo) * 100;
  return `${p < 10 && p > 0 ? p.toFixed(1).replace(".", ",") : Math.round(p)}%`;
}

function haQuanto(iso) {
  if (!iso) return null;
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 2) return "agora há pouco";
  if (min < 60) return `há ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `há ${h}h`;
  return `há ${Math.round(h / 24)} dias`;
}

function Etapa({ rotulo, valor, nota, testid, destaque }) {
  return (
    <div className={`border rounded-lg px-4 py-3 bg-surface ${destaque ? "border-info" : "border-line"}`} data-testid={testid}>
      <div className="text-sm font-medium text-ink-3">{rotulo}</div>
      <div className="font-heading text-2xl font-semibold tabular-nums">{valor}</div>
      <div className="text-[11px] text-ink-3 mt-0.5 min-h-[1rem]">{nota}</div>
    </div>
  );
}

/** Barra proporcional dentro da linha da tabela: magnitude sem virar gráfico. */
function BarraLinha({ valor, maximo }) {
  const pct = maximo ? Math.max(2, (valor / maximo) * 100) : 0;
  return (
    <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden mt-1">
      <div className="h-full" style={{ width: `${pct}%`, background: "var(--serie-1)" }} />
    </div>
  );
}

function Cartao({ icone: Icone, titulo, hint, children, testid }) {
  return (
    <section className="border border-line bg-surface p-5 rounded-lg" data-testid={testid}>
      <div className="flex items-center gap-2 mb-1">
        <Icone size={15} weight="regular" className="text-ink-3" />
        <h2 className="text-sm font-semibold text-ink">{titulo}</h2>
      </div>
      {hint && <p className="text-xs text-ink-3 mb-3">{hint}</p>}
      <div className={hint ? "" : "mt-3"}>{children}</div>
    </section>
  );
}

function Vazio({ children }) {
  return <p className="text-xs text-ink-3 py-4 text-center">{children}</p>;
}

function Origens({ linhas }) {
  if (!linhas.length) return <Vazio>Nenhuma visita no período.</Vazio>;
  const max = Math.max(1, ...linhas.map((l) => l.visitantes));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="desempenho-origens">
        <thead>
          <tr className="text-left text-xs text-ink-3 border-b border-line">
            <th className="py-2 pr-3 font-medium">Origem</th>
            <th className="py-2 px-3 font-medium text-right">Visitantes</th>
            <th className="py-2 px-3 font-medium text-right">WhatsApp</th>
            <th className="py-2 px-3 font-medium text-right">Taxa</th>
            <th className="py-2 px-3 font-medium text-right">Conversas</th>
            <th className="py-2 px-3 font-medium text-right">Reservas</th>
            <th className="py-2 pl-3 font-medium text-right">Receita</th>
          </tr>
        </thead>
        <tbody>
          {linhas.map((l) => (
            <tr key={l.fonte} className="border-b border-line last:border-0">
              <td className="py-2 pr-3 min-w-[160px]">
                {l.fonte}
                <BarraLinha valor={l.visitantes} maximo={max} />
              </td>
              <td className="py-2 px-3 text-right tabular-nums">{n(l.visitantes)}</td>
              <td className="py-2 px-3 text-right tabular-nums">{n(l.whatsapp)}</td>
              <td className="py-2 px-3 text-right tabular-nums">{porcento(l.whatsapp, l.visitantes) ?? "—"}</td>
              <td className="py-2 px-3 text-right tabular-nums">{n(l.conversas)}</td>
              <td className="py-2 px-3 text-right tabular-nums">{n(l.reservas)}</td>
              <td className="py-2 pl-3 text-right tabular-nums">{l.receita ? formatBRL(l.receita) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Paginas({ linhas }) {
  if (!linhas.length) return <Vazio>Nenhuma página vista no período.</Vazio>;
  const max = Math.max(1, ...linhas.map((l) => l.visitas));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="desempenho-paginas">
        <thead>
          <tr className="text-left text-xs text-ink-3 border-b border-line">
            <th className="py-2 pr-3 font-medium">Página</th>
            <th className="py-2 px-3 font-medium text-right">Visitas</th>
            <th className="py-2 px-3 font-medium text-right">WhatsApp</th>
            <th className="py-2 pl-3 font-medium text-right">Taxa</th>
          </tr>
        </thead>
        <tbody>
          {linhas.map((l) => (
            <tr key={l.pagina} className="border-b border-line last:border-0">
              <td className="py-2 pr-3 min-w-[160px]">
                <div className="truncate max-w-[280px]" title={l.pagina}>{l.nome}</div>
                <BarraLinha valor={l.visitas} maximo={max} />
              </td>
              <td className="py-2 px-3 text-right tabular-nums">{n(l.visitas)}</td>
              <td className="py-2 px-3 text-right tabular-nums">{n(l.whatsapp)}</td>
              <td className="py-2 pl-3 text-right tabular-nums">{porcento(l.whatsapp, l.visitas) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CliquesInternos({ linhas }) {
  const [aberto, setAberto] = useState(null);
  if (!linhas.length) return <Vazio>Nenhum clique interno no período.</Vazio>;
  const max = Math.max(1, ...linhas.map((l) => l.n));
  return (
    <ul className="divide-y divide-line" data-testid="desempenho-cliques">
      {linhas.map((l) => (
        <li key={l.alvo} className="py-2">
          <button type="button" className="w-full text-left disabled:cursor-default"
            disabled={!l.detalhes.length} onClick={() => setAberto(aberto === l.alvo ? null : l.alvo)}
            aria-expanded={aberto === l.alvo}>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span>{ALVOS[l.alvo] || l.alvo}{l.detalhes.length > 0 && <span className="text-ink-3"> {aberto === l.alvo ? "▴" : "▾"}</span>}</span>
              <span className="tabular-nums">{n(l.n)}</span>
            </div>
            <BarraLinha valor={l.n} maximo={max} />
          </button>
          {aberto === l.alvo && (
            <ul className="mt-2 ml-3 space-y-1">
              {l.detalhes.map((d) => (
                <li key={d.detalhe} className="flex justify-between gap-3 text-xs text-ink-2">
                  <span className="truncate">{d.detalhe}</span>
                  <span className="tabular-nums">{n(d.n)}</span>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function Parcelas({ linhas, chave, nomes }) {
  const total = linhas.reduce((s, l) => s + l.visitas, 0);
  if (!total) return <Vazio>Sem visitas no período.</Vazio>;
  return (
    <ul className="space-y-2">
      {linhas.map((l) => (
        <li key={l[chave]}>
          <div className="flex justify-between text-sm">
            <span>{nomes[l[chave]] || l[chave]}</span>
            <span className="tabular-nums text-ink-2">{porcento(l.visitas, total)}</span>
          </div>
          <BarraLinha valor={l.visitas} maximo={total} />
        </li>
      ))}
    </ul>
  );
}

/**
 * Monta o link com origem para a bio do Instagram, o anúncio ou a mensagem.
 * Sem a etiqueta, quem clica no link da bio aparece como "Instagram" na melhor
 * das hipóteses e como "Direto" na pior (o app do Instagram nem sempre manda
 * de onde veio).
 */
function MontarLink({ url }) {
  const [fonte, setFonte] = useState("instagram");
  const [campanha, setCampanha] = useState("");
  const [pago, setPago] = useState(false);
  const link = useMemo(() => {
    if (!url) return null;
    const q = new URLSearchParams({ utm_source: fonte, utm_medium: pago ? "cpc" : "social" });
    const c = campanha.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    if (c) q.set("utm_campaign", c);
    return `${url}/?${q.toString()}`;
  }, [url, fonte, campanha, pago]);

  const copiar = () => {
    navigator.clipboard.writeText(link).then(() => toast.success("Link copiado"))
      .catch(() => toast.error("Não foi possível copiar"));
  };

  return (
    <Cartao icone={LinkSimple} titulo="Link com origem" testid="desempenho-montar-link"
      hint="Use este link na bio, nos anúncios e nas mensagens. É ele que faz a visita aparecer com a origem certa na tabela acima.">
      {!url ? (
        <Vazio>O site ainda não tem endereço. <Link to="/app/site" className="text-brand underline">Configurar o site</Link></Vazio>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 items-end">
          <label className="text-xs text-ink-3">Onde o link vai ser usado
            <select className="zz-input mt-1" value={fonte} onChange={(e) => setFonte(e.target.value)}
              data-testid="link-fonte">
              {FONTES_LINK.map(([v, r]) => <option key={v} value={v}>{r}</option>)}
            </select>
          </label>
          <label className="text-xs text-ink-3">Campanha (opcional)
            <input className="zz-input mt-1" value={campanha} maxLength={60} placeholder="ex.: alta temporada"
              onChange={(e) => setCampanha(e.target.value)} data-testid="link-campanha" />
          </label>
          <label className="flex items-center gap-2 text-sm text-ink-2 pb-2">
            <input type="checkbox" checked={pago} onChange={(e) => setPago(e.target.checked)} data-testid="link-pago" />
            É anúncio pago
          </label>
          <div className="sm:col-span-3 flex gap-2">
            <input className="zz-input font-mono text-xs flex-1" readOnly value={link} data-testid="link-gerado"
              onFocus={(e) => e.target.select()} />
            <button type="button" className="zz-btn zz-btn-secondary" onClick={copiar} data-testid="link-copiar">
              <Copy size={14} weight="regular" /> Copiar
            </button>
          </div>
        </div>
      )}
    </Cartao>
  );
}

export default function DesempenhoSitePage() {
  const [janela, setJanela] = useState("30");
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState(null);

  const carregar = useCallback(() => {
    setErro(null);
    api.get("/site/desempenho", { params: { dias: janela } })
      .then((r) => setDados(r.data))
      .catch((ex) => setErro(formatApiErrorDetail(ex.response?.data?.detail)));
  }, [janela]);

  useEffect(() => { carregar(); }, [carregar]);

  const t = dados?.totais;
  const ultimo = haQuanto(dados?.site?.ultimo_evento);

  return (
    <div className="zz-page" data-testid="desempenho-site-page">
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Desempenho do site</h1>
          <p className="text-ink-2 mt-2 text-sm">
            Quem visitou, de onde veio, o que clicou e quantos viraram conversa e reserva.
          </p>
        </div>
        <div className="flex gap-1">
          {JANELAS.map(([v, r]) => (
            <button key={v} onClick={() => setJanela(v)} data-testid={`janela-${v}`}
              className={`px-3 py-1.5 text-xs font-medium border rounded-full ${
                janela === v ? "bg-brand text-brand-fg border-brand" : "bg-surface border-line text-ink-2"
              }`}>
              {r}
            </button>
          ))}
        </div>
      </div>

      {erro && (
        <div className="border border-warning bg-warning-soft p-4 flex gap-3 rounded-lg mb-4">
          <Warning size={18} weight="regular" className="text-warning shrink-0 mt-0.5" />
          <div className="text-sm text-ink">{erro}</div>
        </div>
      )}
      {!dados && !erro && <div className="text-xs text-ink-3 py-6">Carregando…</div>}

      {dados && (
        <>
          {!dados.site.publicado ? (
            <div className="border border-warning bg-warning-soft p-4 flex gap-3 rounded-lg mb-4" data-testid="desempenho-aviso">
              <Warning size={18} weight="regular" className="text-warning shrink-0 mt-0.5" />
              <div className="text-sm text-ink">
                O site não está publicado, então nada está sendo medido.{" "}
                <Link to="/app/site" className="underline">Publicar o site</Link>
              </div>
            </div>
          ) : (
            <p className="text-xs text-ink-3 mb-4" data-testid="desempenho-status">
              {ultimo ? `Medindo. Última visita ou clique registrado ${ultimo}.` : "Medindo. Nenhuma visita registrada ainda."}
              {dados.site.url && <> · <a href={dados.site.url} target="_blank" rel="noopener noreferrer" className="underline">{dados.site.url.replace(/^https?:\/\//, "")}</a></>}
            </p>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 mb-4" data-testid="desempenho-funil">
            <Etapa rotulo="Visitantes" valor={n(t.visitantes)} nota={`${n(t.visitas)} páginas vistas`}
              testid="funil-visitantes" destaque />
            <Etapa rotulo="Cliques no WhatsApp" valor={n(t.whatsapp)}
              nota={porcento(t.whatsapp, t.visitantes) && `${porcento(t.whatsapp, t.visitantes)} dos visitantes`}
              testid="funil-whatsapp" />
            <Etapa rotulo="Conversas" valor={n(t.conversas)}
              nota={porcento(t.conversas, t.whatsapp) && `${porcento(t.conversas, t.whatsapp)} dos cliques`}
              testid="funil-conversas" />
            <Etapa rotulo="Orçamentos" valor={n(t.orcamentos)} nota="abertos no funil" testid="funil-orcamentos" />
            <Etapa rotulo="Reservas" valor={n(t.reservas)} nota={t.receita ? formatBRL(t.receita) : null}
              testid="funil-reservas" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-4">
            <SerieDiaria titulo="Visitantes por dia" dados={dados.serie}
              series={[{ chave: "visitantes", rotulo: "Visitantes", slot: 1 }]} />
            <SerieDiaria titulo="Cliques no WhatsApp por dia" dados={dados.serie}
              series={[{ chave: "whatsapp", rotulo: "Cliques no WhatsApp", slot: 2 }]} />
          </div>

          <div className="mb-4">
            <Cartao icone={Compass} titulo="De onde vêm os visitantes" testid="desempenho-bloco-origens"
              hint="A conversa e a reserva voltam para a origem da visita em que o turista clicou no WhatsApp. Reserva cancelada não conta.">
              <Origens linhas={dados.origens} />
            </Cartao>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-4">
            <Cartao icone={FileText} titulo="Páginas" testid="desempenho-bloco-paginas">
              <Paginas linhas={dados.paginas} />
            </Cartao>
            <Cartao icone={CursorClick} titulo="Cliques dentro do site" testid="desempenho-bloco-cliques"
              hint={`${n(t.cliques_internos)} no período. Toque numa linha para ver em quê.`}>
              <CliquesInternos linhas={dados.cliques} />
            </Cartao>
          </div>

          {dados.campanhas.length > 0 && (
            <div className="mb-4">
              <Cartao icone={Megaphone} titulo="Campanhas" testid="desempenho-bloco-campanhas"
                hint="Visitas que chegaram por um link com campanha (utm_campaign).">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-ink-3 border-b border-line">
                        <th className="py-2 pr-3 font-medium">Campanha</th>
                        <th className="py-2 px-3 font-medium">Origem</th>
                        <th className="py-2 px-3 font-medium text-right">Visitas</th>
                        <th className="py-2 px-3 font-medium text-right">WhatsApp</th>
                        <th className="py-2 pl-3 font-medium text-right">Conversas</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dados.campanhas.map((c) => (
                        <tr key={`${c.fonte}|${c.campanha}`} className="border-b border-line last:border-0">
                          <td className="py-2 pr-3">{c.campanha}</td>
                          <td className="py-2 px-3 text-ink-2">{c.fonte}</td>
                          <td className="py-2 px-3 text-right tabular-nums">{n(c.visitas)}</td>
                          <td className="py-2 px-3 text-right tabular-nums">{n(c.whatsapp)}</td>
                          <td className="py-2 pl-3 text-right tabular-nums">{n(c.conversas)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Cartao>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
            <Cartao icone={Translate} titulo="Idioma do site" testid="desempenho-bloco-idiomas">
              <Parcelas linhas={dados.idiomas} chave="idioma" nomes={IDIOMAS} />
            </Cartao>
            <Cartao icone={DeviceMobile} titulo="Aparelho" testid="desempenho-bloco-aparelhos">
              <Parcelas linhas={dados.dispositivos} chave="dispositivo" nomes={APARELHOS} />
            </Cartao>
          </div>

          <div className="mb-4">
            <MontarLink url={dados.site.url} />
          </div>

          <p className="text-[11px] text-ink-3">
            Dias no fuso da agência ({dados.fuso}). Visitante é contado uma vez por dia, sem cookie e
            sem guardar IP; robôs de busca não entram. As visitas e os cliques ficam guardados por 90
            dias. A visita feita pela pré-visualização não conta.
          </p>
        </>
      )}
    </div>
  );
}
