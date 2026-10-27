import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowSquareOut, CaretDown, Eye, CaretRight, ChartBar, Copy, Globe, IdentificationCard,
  ImageSquare, MapPin, PencilSimple, Phone, Question, ShieldCheck, Sparkle, Star, Warning, Clock,
  MagnifyingGlass, CheckCircle, Plus, Trash, Translate, BookOpenText,
} from "@phosphor-icons/react";
import { CampoHora, REGEX_RELOGIO } from "@/components/agenda/CampoHora";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { confirmToast } from "@/lib/confirm-toast";
import Modal from "@/components/ui/Modal";
import {
  CampoComPrefixo, CamposSitePasseio, EditorPerguntas, ImagemUnica, ListaItens, ListaObjetos,
  corpoServico, limpaPerguntas, paraSlug, problemaPerguntas, problemaSite, slugFinal, urlMidia,
} from "@/components/site/comum";

// Tela de configuração do site da agência (fase 3).
//
// O site em si é HTML gerado pelo backend; aqui mora só o que a agência
// decide: endereço, identidade, contato, domínio próprio, quais passeios
// aparecem, e o resultado (cliques no botão do WhatsApp que viraram conversa).
//
// **A URL do site vem pronta do backend** (`url`, `url_dominio_proprio`): o
// endereço depende de variável de ambiente do servidor (domínio base, porta
// local), e montar aqui faria a tela mostrar um link que não abre. O prefixo e
// o sufixo em volta do campo de slug são só desenho.

const MAX_CHAMADA = 90;
const MAX_SOBRE = 2000;
const REGEX_SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const REGEX_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_CADASTUR = 40;
const MAX_DIFERENCIAIS = 6;
const MAX_DIFERENCIAL = 80;
const MAX_FONTE = 40;
const MAX_LINK = 300;
const MAX_DEPOIMENTOS = 12;
const MAX_DEP_NOME = 60;
const MAX_DEP_ORIGEM = 60;
const MAX_DEP_TEXTO = 400;
const MAX_PERGUNTAS_SITE = 20;
const MAX_PAGAMENTO = 8;
const MAX_PAGAMENTO_ITEM = 60;
const MAX_CANCELAMENTO = 800;
const MAX_RUA = 120;
const MAX_BAIRRO = 60;
const MAX_CIDADE = 60;
const MAX_HORARIOS = 7;

const UFS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
  "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
];

// 0 = segunda … 6 = domingo, como o backend guarda.
const DIAS_CURTOS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

// Português é sempre o principal; estes são os extras que a agência liga.
const IDIOMAS = [
  { id: "en", nome: "Inglês", ver: "Ver em inglês" },
  { id: "es", nome: "Espanhol", ver: "Ver em espanhol" },
];

/** "65590000" → "65590-000", progressivo enquanto digita. */
function mascaraCep(v) {
  const d = String(v || "").replace(/\D/g, "").slice(0, 8);
  return d.length <= 5 ? d : `${d.slice(0, 5)}-${d.slice(5)}`;
}

/** Faixas de horário sem as linhas totalmente vazias (as que a pessoa
 * acrescentou e não preencheu), com os dias em ordem. */
function limpaHorarios(l) {
  return (l || [])
    .map((h) => ({
      dias: [...new Set(h.dias || [])].sort((a, b) => a - b),
      abre: h.abre || "",
      fecha: h.fecha || "",
    }))
    .filter((h) => h.dias.length || h.abre || h.fecha);
}

/** "5598981112233" ou "98981112233" → "(98) 98111-2233", progressivo enquanto digita.
 *
 * Mostra só o número nacional: o DDI 55 é posto pelo backend, que normaliza
 * como o resto do sistema. Nunca acrescenta dígito. */
function mascaraTelefone(v) {
  let d = String(v || "").replace(/\D/g, "");
  if (d.startsWith("55") && d.length > 11) d = d.slice(2);
  d = d.slice(0, 11);
  if (!d) return "";
  if (d.length <= 2) return `(${d}`;
  if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`;
  if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
}

function limpaInstagram(v) {
  return String(v || "")
    .trim()
    .replace(/^https?:\/\/(www\.)?instagram\.com\//i, "")
    .replace(/^@+/, "")
    .replace(/[/?#].*$/, "")
    .replace(/\s/g, "");
}

function limpaDominio(v) {
  return String(v || "")
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/\/.*$/, "");
}

function paraForm(site) {
  const s = site || {};
  return {
    slug: s.slug || "",
    nome: s.nome || "",
    chamada: s.chamada || "",
    sobre: s.sobre || "",
    whatsapp: mascaraTelefone(s.whatsapp),
    instagram: s.instagram || "",
    email: s.email || "",
    endereco_rua: s.endereco_rua || "",
    endereco_bairro: s.endereco_bairro || "",
    cidade: s.cidade || "",
    uf: s.uf || "",
    cep: mascaraCep(s.cep),
    // O campo guarda o que foi colado; a posição salva vem de latitude/longitude.
    // Só vai no corpo se a pessoa mexeu, para não reenviar a localização a cada salvar.
    coordenadas: "",
    coordenadas_mexeu: false,
    tinha_coordenadas: s.latitude != null && s.longitude != null,
    horarios: (s.horarios || []).map((h) => ({ dias: [...(h.dias || [])], abre: h.abre || "", fecha: h.fecha || "" })),
    logo_id: s.logo_id || null,
    capa_id: s.capa_id || null,
    dominio_proprio: s.dominio_proprio || "",
    cadastur: s.cadastur || "",
    desde: s.desde != null ? String(s.desde) : "",
    diferenciais: s.diferenciais || [],
    nota_avaliacoes: s.nota_avaliacoes != null ? String(s.nota_avaliacoes).replace(".", ",") : "",
    total_avaliacoes: s.total_avaliacoes != null ? String(s.total_avaliacoes) : "",
    link_avaliacoes: s.link_avaliacoes || "",
    fonte_avaliacoes: s.fonte_avaliacoes || "",
    depoimentos: (s.depoimentos || []).map((d) => ({
      nome: d.nome || "", origem: d.origem || "", texto: d.texto || "", nota: d.nota ?? null,
    })),
    faq: (s.faq || []).map((p) => ({ pergunta: p.pergunta || "", resposta: p.resposta || "" })),
    pagamento: s.pagamento || [],
    cancelamento: s.cancelamento || "",
    mostrar_embarque: s.mostrar_embarque !== false,
    google_verificacao: s.google_verificacao || "",
  };
}

/** "4,8" → 4.8; vazio → null; ilegível → NaN (recusado em `problemaForm`). */
function paraNumeroOuNulo(v) {
  const t = String(v ?? "").trim().replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : NaN;
}

/** Uma casa decimal, como o site mostra (o backend arredonda igual). */
function arredonda1(n) {
  return n == null || Number.isNaN(n) ? n : Math.round(n * 10) / 10;
}

function limpaLista(l) {
  return (l || []).map((x) => String(x).trim()).filter(Boolean);
}

function limpaDepoimentos(l) {
  return (l || [])
    .map((d) => ({
      nome: (d.nome || "").trim(),
      origem: (d.origem || "").trim() || null,
      texto: (d.texto || "").trim(),
      nota: d.nota ?? null,
    }))
    .filter((d) => d.nome || d.texto || d.origem);
}

/** O corpo do `PUT /api/site`. O slug vazio fica de fora: mandá-lo nulo
 * apagaria o endereço de um site que já tem um.
 *
 * `endereco` e `horario` (os textos antigos) também ficam de fora: a tela não
 * os edita mais, e o backend só os usa enquanto os estruturados estão vazios. */
function paraCorpo(f) {
  const nulo = (v) => (String(v || "").trim() || null);
  const corpo = {
    nome: nulo(f.nome),
    chamada: nulo(f.chamada),
    sobre: nulo(f.sobre),
    whatsapp: f.whatsapp.replace(/\D/g, "") || null,
    instagram: limpaInstagram(f.instagram) || null,
    email: nulo(f.email),
    endereco_rua: nulo(f.endereco_rua),
    endereco_bairro: nulo(f.endereco_bairro),
    cidade: nulo(f.cidade),
    uf: nulo(f.uf),
    cep: nulo(f.cep),
    horarios: limpaHorarios(f.horarios),
    logo_id: f.logo_id || null,
    capa_id: f.capa_id || null,
    dominio_proprio: limpaDominio(f.dominio_proprio) || null,
    cadastur: nulo(f.cadastur),
    desde: paraNumeroOuNulo(f.desde),
    diferenciais: limpaLista(f.diferenciais),
    nota_avaliacoes: arredonda1(paraNumeroOuNulo(f.nota_avaliacoes)),
    total_avaliacoes: paraNumeroOuNulo(f.total_avaliacoes),
    link_avaliacoes: nulo(f.link_avaliacoes),
    fonte_avaliacoes: nulo(f.fonte_avaliacoes),
    depoimentos: limpaDepoimentos(f.depoimentos),
    faq: limpaPerguntas(f.faq, MAX_PERGUNTAS_SITE),
    pagamento: limpaLista(f.pagamento),
    cancelamento: nulo(f.cancelamento),
    mostrar_embarque: f.mostrar_embarque !== false,
    // A tag inteira ou só o código: o backend guarda só o código.
    google_verificacao: nulo(f.google_verificacao),
  };
  const slug = slugFinal(f.slug);
  if (slug) corpo.slug = slug;
  // Vazio com localização salva = tirar; vazio sem nada salvo não muda nada.
  const coords = (f.coordenadas || "").trim();
  if (f.coordenadas_mexeu && (coords || f.tinha_coordenadas)) corpo.coordenadas = coords;
  return corpo;
}

/** O que impede de salvar os campos novos, dito antes do 400. */
function problemaForm(f) {
  const ano = new Date().getFullYear();
  const desde = paraNumeroOuNulo(f.desde);
  if (desde != null && !(Number.isInteger(desde) && desde >= 1900 && desde <= ano)) {
    return `Ano de fundação: um ano entre 1900 e ${ano}`;
  }
  const nota = paraNumeroOuNulo(f.nota_avaliacoes);
  if (nota != null && !(nota >= 1 && nota <= 5)) return "Nota das avaliações: de 1 a 5, como 4,8";
  const total = paraNumeroOuNulo(f.total_avaliacoes);
  if (total != null && !(Number.isInteger(total) && total >= 0)) return "Total de avaliações: um número inteiro";
  const link = f.link_avaliacoes.trim();
  if (link && !/^https:\/\/[^\s<>"']+$/i.test(link)) return "Link das avaliações: cole o endereço completo, começando com https://";
  const dep = limpaDepoimentos(f.depoimentos).findIndex((d) => !d.nome || !d.texto);
  if (dep >= 0) return `Depoimento ${dep + 1}: precisa do nome e do texto`;
  const cep = String(f.cep || "").replace(/\D/g, "");
  if (cep && cep.length !== 8) return "CEP: 8 dígitos, como 65590-000";
  const horarios = limpaHorarios(f.horarios);
  if (horarios.length > MAX_HORARIOS) return `Horário de atendimento: no máximo ${MAX_HORARIOS} faixas`;
  for (let i = 0; i < horarios.length; i += 1) {
    const h = horarios[i];
    const nome = horarios.length > 1 ? `Horário de atendimento (faixa ${i + 1})` : "Horário de atendimento";
    if (!h.dias.length) return `${nome}: escolha os dias`;
    if (!REGEX_RELOGIO.test(h.abre) || !REGEX_RELOGIO.test(h.fecha)) return `${nome}: informe a hora de abrir e a de fechar`;
    if (h.fecha <= h.abre) return `${nome}: fecha às ${h.fecha}, antes de abrir às ${h.abre}`;
  }
  return problemaPerguntas(f.faq, "Perguntas frequentes");
}

function porcento(parte, todo) {
  if (!todo) return null;
  return `${Math.round((parte / todo) * 100)}%`;
}

function Bloco({ icone: Icone, titulo, hint, children, testid }) {
  return (
    <section className="border border-line bg-surface p-6 mb-4 rounded-xl" data-testid={testid}>
      <div className="flex items-center gap-2 mb-1">
        <Icone size={16} weight="regular" className="text-ink-3" />
        <h2 className="font-heading text-lg">{titulo}</h2>
      </div>
      {hint && <p className="text-xs text-ink-3 mb-4">{hint}</p>}
      <div className={hint ? "" : "mt-4"}>{children}</div>
    </section>
  );
}

function Campo({ label, children, hint, contador }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-sm font-medium text-ink-2">{label}</label>
        {contador}
      </div>
      {children}
      {hint && <div className="text-[11px] text-ink-3 mt-1">{hint}</div>}
    </div>
  );
}

function Contador({ n, max }) {
  return (
    <span className={`text-[11px] tabular-nums ${n > max ? "text-danger" : "text-ink-3"}`}>
      {n}/{max}
    </span>
  );
}

async function copiar(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    toast.success("Copiado");
  } catch {
    toast.error("Não foi possível copiar. Selecione o link e copie à mão.");
  }
}

/** Abre o site pelo endereço com token assinado (`url_previa`), que mostra o
 * site mesmo em rascunho, com uma faixa de pré-visualização. Enquanto não
 * está publicado, o link público dá 404 para todo mundo, inclusive a agência. */
function BotaoPrevia({ url }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" data-testid="site-previa"
      className="zz-btn zz-btn-secondary text-xs py-1">
      <Eye size={13} weight="regular" /> Pré-visualizar
    </a>
  );
}

// ---------------------------------------------------------------------------
// Cabeçalho: estado, link e publicar
// ---------------------------------------------------------------------------

function Cabecalho({ dados, sujo, onPublicar, publicando }) {
  const { site, url, url_dominio_proprio: urlProprio, url_previa: urlPrevia, pronto } = dados;
  const publicado = !!site.publicado;
  const faltando = pronto?.faltando || [];
  const podePublicar = !!pronto?.publicavel && !sujo;

  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-heading text-4xl font-semibold">Site</h1>
            <span data-testid="site-status"
              className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
                publicado ? "bg-success-soft text-success border-success" : "bg-surface-3 text-ink-2 border-line"}`}>
              {publicado ? "Publicado" : "Rascunho"}
            </span>
          </div>
          <p className="text-sm text-ink-2 mt-2 max-w-2xl">
            A vitrine dos passeios. Cada passeio tem um botão "Quero esse passeio" que abre o
            WhatsApp da agência com um código, e a conversa chega já com o cliente e o orçamento.
          </p>
          {url && (
            <div className="flex flex-wrap items-center gap-2 mt-3">
              <a href={url} target="_blank" rel="noopener noreferrer" data-testid="site-url"
                className="text-sm text-brand font-medium inline-flex items-center gap-1 break-all hover:underline">
                {url} <ArrowSquareOut size={14} weight="regular" />
              </a>
              <button type="button" className="zz-btn zz-btn-secondary text-xs py-1"
                data-testid="site-copiar-link" onClick={() => copiar(url)}>
                <Copy size={13} weight="regular" /> Copiar link
              </button>
              {urlPrevia && <BotaoPrevia url={urlPrevia} />}
            </div>
          )}
          {!url && urlPrevia && (
            <div className="mt-3"><BotaoPrevia url={urlPrevia} /></div>
          )}
          {urlProprio && (
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <a href={urlProprio} target="_blank" rel="noopener noreferrer" data-testid="site-url-proprio"
                className="text-sm text-brand inline-flex items-center gap-1 break-all hover:underline">
                {urlProprio} <ArrowSquareOut size={14} weight="regular" />
              </a>
              <button type="button" className="zz-btn zz-btn-secondary text-xs py-1"
                data-testid="site-copiar-link-proprio" onClick={() => copiar(urlProprio)}>
                <Copy size={13} weight="regular" /> Copiar
              </button>
            </div>
          )}
          {!publicado && url && (
            <p className="text-[11px] text-ink-3 mt-1">
              Enquanto for rascunho, o endereço não abre para o público. Use Pré-visualizar
              para ver como o site está ficando.
            </p>
          )}
        </div>

        <div className="shrink-0">
          {publicado ? (
            <button type="button" className="zz-btn zz-btn-secondary px-5 py-2.5" data-testid="site-despublicar"
              disabled={publicando} onClick={() => onPublicar(false)}>
              {publicando ? "Salvando…" : "Despublicar"}
            </button>
          ) : (
            <button type="button" className="zz-btn px-6 py-2.5 text-base" data-testid="site-publicar"
              disabled={publicando || !podePublicar} onClick={() => onPublicar(true)}>
              <Globe size={18} weight="regular" /> {publicando ? "Publicando…" : "Publicar"}
            </button>
          )}
        </div>
      </div>

      {!publicado && sujo && pronto?.publicavel && (
        <p className="text-xs text-warning mt-3">Salve as alterações antes de publicar.</p>
      )}
      {!publicado && faltando.length > 0 && (
        <div className="mt-4 border border-warning bg-warning-soft rounded-xl p-4" data-testid="site-faltando">
          <div className="flex items-center gap-2 text-sm font-medium text-warning">
            <Warning size={16} weight="regular" /> Para publicar, falta:
          </div>
          <ul className="list-disc pl-6 mt-2 text-sm text-ink-2 space-y-0.5">
            {faltando.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Endereço (slug)
// ---------------------------------------------------------------------------

function Endereco({ dados, form, setForm }) {
  const salvo = dados.site.slug || "";
  const [conf, setConf] = useState({ estado: "igual" });
  const slug = slugFinal(form.slug);

  // Conferência com espera: uma ida ao servidor por pausa na digitação, não
  // por tecla. O `vivo` descarta a resposta de uma conferência que ficou para
  // trás, senão "lenc" respondendo depois de "lencois" mostraria o estado errado.
  useEffect(() => {
    if (!slug || slug === salvo) { setConf({ estado: "igual" }); return undefined; }
    if (slug.length < 3 || !REGEX_SLUG.test(slug)) { setConf({ estado: "invalido" }); return undefined; }
    let vivo = true;
    setConf({ estado: "conferindo" });
    const t = setTimeout(async () => {
      try {
        const r = await api.get("/site/slug-livre", { params: { slug } });
        if (!vivo) return;
        setConf(r.data?.livre
          ? { estado: "livre" }
          : { estado: "em_uso", motivo: r.data?.motivo, sugestao: r.data?.sugestao });
      } catch (e) {
        if (vivo) setConf({ estado: "erro", motivo: formatApiErrorDetail(e.response?.data?.detail) });
      }
    }, 450);
    return () => { vivo = false; clearTimeout(t); };
  }, [slug, salvo]);

  const [sugerir, sugerindo] = useAcao(async () => {
    try {
      const r = await api.get("/site/sugestao-slug");
      if (r.data?.slug) setForm((f) => ({ ...f, slug: r.data.slug }));
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const protocolo = (dados.url || "").startsWith("http://") ? "http://" : "https://";
  const trocandoPublicado = dados.site.publicado && salvo && slug && slug !== salvo;

  return (
    <Bloco icone={Globe} titulo="Endereço" testid="site-endereco"
      hint="O nome da agência no endereço do site. Letras minúsculas, números e hífen.">
      <div className="flex flex-col sm:flex-row gap-2">
        <div className="flex-1 min-w-0">
          <CampoComPrefixo prefixo={protocolo} sufixo={`.${dados.dominio_base}`}
            valor={form.slug} testid="site-slug" placeholder="sua-agencia"
            onChange={(v) => setForm((f) => ({ ...f, slug: paraSlug(v) }))} />
        </div>
        <button type="button" className="zz-btn zz-btn-secondary shrink-0" data-testid="site-sugerir-slug"
          disabled={sugerindo} onClick={() => sugerir()}>
          <Sparkle size={14} weight="regular" /> {sugerindo ? "Sugerindo…" : "Sugerir"}
        </button>
      </div>

      <div className="text-xs mt-2 min-h-[1.25rem]" data-testid="site-slug-status">
        {conf.estado === "conferindo" && <span className="text-ink-3">Conferindo…</span>}
        {conf.estado === "livre" && <span className="text-success">Disponível</span>}
        {conf.estado === "invalido" && (
          <span className="text-danger">
            De 3 a 40 caracteres: letras minúsculas, números e hífen, sem hífen no começo ou no fim.
          </span>
        )}
        {conf.estado === "erro" && <span className="text-danger">{conf.motivo}</span>}
        {conf.estado === "em_uso" && (
          <span className="text-danger">
            {conf.motivo || "Este endereço já está em uso."}
            {conf.sugestao && (
              <>
                {" "}Que tal{" "}
                <button type="button" className="underline font-medium text-brand"
                  data-testid="site-usar-sugestao"
                  onClick={() => setForm((f) => ({ ...f, slug: conf.sugestao }))}>
                  {conf.sugestao}
                </button>
                ?
              </>
            )}
          </span>
        )}
      </div>

      {trocandoPublicado && (
        <div className="mt-2 border border-warning bg-warning-soft rounded-lg p-3 text-xs text-ink-2"
          data-testid="site-aviso-troca-slug">
          <strong className="text-warning">O site já está publicado.</strong> Ao salvar, o endereço antigo
          (<span className="tabular-nums">{salvo}</span>) deixa de funcionar e links já divulgados, no
          Instagram, em cartões ou no Google, param de abrir.
        </div>
      )}
    </Bloco>
  );
}

// ---------------------------------------------------------------------------
// Identidade e contato
// ---------------------------------------------------------------------------

function Identidade({ form, setForm }) {
  const mexer = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  return (
    <Bloco icone={IdentificationCard} titulo="Identidade" testid="site-identidade">
      <div className="space-y-4">
        <Campo label="Nome exibido" hint="Em branco, usa o nome da agência.">
          <input className="zz-input" value={form.nome} data-testid="site-nome" onChange={mexer("nome")}
            placeholder="Nome da agência" />
        </Campo>
        <Campo label="Chamada" contador={<Contador n={form.chamada.length} max={MAX_CHAMADA} />}
          hint="O título grande do topo do site.">
          <input className="zz-input" value={form.chamada} maxLength={MAX_CHAMADA} data-testid="site-chamada"
            placeholder="Passeios nos Lençóis Maranhenses" onChange={mexer("chamada")} />
        </Campo>
        <Campo label="Sobre a agência" contador={<Contador n={form.sobre.length} max={MAX_SOBRE} />}>
          <textarea className="zz-input min-h-[120px]" value={form.sobre} maxLength={MAX_SOBRE}
            data-testid="site-sobre" onChange={mexer("sobre")}
            placeholder="Há quanto tempo a agência existe, de onde saem os passeios, o que a diferencia." />
        </Campo>
        <div className="grid grid-cols-1 sm:grid-cols-[200px_1fr] gap-4">
          <Campo label="Logo">
            <ImagemUnica id={form.logo_id} rotulo="Logo" testid="site-logo" proporcao="aspect-square"
              ajuda="Também é o ícone da aba do navegador e do Google. Quadrado fica melhor. Sem logo, a aba mostra a inicial da agência."
              onChange={(id) => setForm((f) => ({ ...f, logo_id: id }))} />
          </Campo>
          <Campo label="Imagem de capa">
            <ImagemUnica id={form.capa_id} rotulo="Capa" testid="site-capa" proporcao="aspect-[16/7]"
              ajuda="A foto do topo do site. Larga e na horizontal: 1600 × 700 ou maior. Sem capa, o topo usa a primeira foto de passeio."
              onChange={(id) => setForm((f) => ({ ...f, capa_id: id }))} />
          </Campo>
        </div>
        <div className="border border-line rounded-lg p-3 bg-surface-2 text-xs text-ink-2 flex items-start gap-2">
          <ImageSquare size={16} weight="regular" className="text-ink-3 shrink-0 mt-0.5" />
          <span>
            As cores e as fontes do site são as da paleta da agência, a mesma do painel.
            Para trocar, escolha outra paleta em{" "}
            <Link to="/app/settings" className="text-brand font-medium underline" data-testid="site-link-paleta">
              Ajustes
            </Link>.
          </span>
        </div>
      </div>
    </Bloco>
  );
}

function Contato({ form, setForm }) {
  const mexer = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  return (
    <Bloco icone={Phone} titulo="Contato" testid="site-contato">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Campo label="WhatsApp do botão" hint="Para onde vai o turista que toca em “Quero esse passeio”.">
          <input className="zz-input tabular-nums" inputMode="tel" value={form.whatsapp}
            data-testid="site-whatsapp" placeholder="(98) 98111-2233"
            onChange={(e) => setForm((f) => ({ ...f, whatsapp: mascaraTelefone(e.target.value) }))} />
        </Campo>
        <Campo label="Instagram" hint="Só o nome do perfil, sem @.">
          <CampoComPrefixo prefixo="@" valor={form.instagram} testid="site-instagram"
            placeholder="suaagencia"
            onChange={(v) => setForm((f) => ({ ...f, instagram: limpaInstagram(v) }))} />
        </Campo>
        <Campo label="E-mail">
          <input className="zz-input" type="email" value={form.email} data-testid="site-email"
            placeholder="contato@suaagencia.com.br" onChange={mexer("email")} />
        </Campo>
      </div>
    </Bloco>
  );
}

/** Nota para o site que ainda tem o endereço ou o horário em texto livre (de
 * antes dos campos estruturados): o texto continua aparecendo até os campos
 * de cima serem preenchidos. */
function NotaTextoAntigo({ texto, testid, oque = "o endereço" }) {
  if (!texto) return null;
  return (
    <div className="border border-line rounded-lg p-3 bg-surface-2 text-xs text-ink-2" data-testid={testid}>
      Hoje o site mostra: «{texto}». Preencha os campos acima para o Google entender {oque}.
    </div>
  );
}

function ComoAparece({ texto, testid }) {
  if (!texto) return null;
  return (
    <p className="text-[11px] text-ink-3" data-testid={testid}>
      Como aparece no site: <span className="text-ink-2">{texto}</span>
    </p>
  );
}

/** Preenche cidade, UF, rua e bairro quando o CEP chega a 8 dígitos
 * (`GET /site/cep/{cep}`, que consulta ViaCEP e BrasilAPI no servidor).
 *
 * Cidade e UF sempre seguem o CEP. Rua e bairro só são trocados se estavam
 * vazios ou ainda com o que o próprio CEP anterior pôs: o que a agência
 * digitou não é apagado por uma consulta. O CEP de cidade inteira (Barreirinhas,
 * Atins: um CEP só para o município) volta sem rua, e o aviso pede o resto.
 * Quem abre a tela com o CEP já salvo não dispara consulta nenhuma. */
function useCepAutomatico(form, setForm) {
  const digitos = String(form.cep || "").replace(/\D/g, "");
  const consultado = useRef(digitos.length === 8 ? digitos : "");
  const posto = useRef({ rua: "", bairro: "" });
  const ruaRef = useRef(null);
  const atual = useRef(form);
  atual.current = form;
  const [estado, setEstado] = useState({ buscando: false, texto: null });

  useEffect(() => {
    if (digitos.length !== 8) {
      if (digitos.length < 8) consultado.current = "";
      setEstado({ buscando: false, texto: null });
      return undefined;
    }
    if (consultado.current === digitos) return undefined;
    consultado.current = digitos;
    let vivo = true;
    setEstado({ buscando: true, texto: "Buscando o endereço…" });
    (async () => {
      try {
        const { data: e } = await api.get(`/site/cep/${digitos}`);
        if (!vivo) return;
        const f = atual.current;
        const ruaNova = e.rua ? `${e.rua}, ` : "";
        const trocaRua = Boolean(ruaNova) && (!f.endereco_rua.trim() || f.endereco_rua === posto.current.rua);
        const trocaBairro = Boolean(e.bairro) && (!f.endereco_bairro.trim() || f.endereco_bairro === posto.current.bairro);
        posto.current = {
          rua: trocaRua ? ruaNova : posto.current.rua,
          bairro: trocaBairro ? e.bairro : posto.current.bairro,
        };
        setForm((g) => ({
          ...g, cidade: e.cidade, uf: e.uf,
          ...(trocaRua ? { endereco_rua: ruaNova } : {}),
          ...(trocaBairro ? { endereco_bairro: e.bairro } : {}),
        }));
        setEstado({
          buscando: false,
          texto: e.rua ? "Endereço preenchido. Falta o número." : `CEP de ${e.cidade} inteira: preencha a rua e o bairro.`,
        });
        // O cursor vai para o fim da rua, onde falta só o número.
        if (trocaRua) {
          setTimeout(() => {
            const el = ruaRef.current;
            if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
          }, 0);
        }
      } catch (err) {
        if (vivo) setEstado({ buscando: false, texto: formatApiErrorDetail(err.response?.data?.detail) });
      } finally {
        vivo = false;
      }
    })();
    // Desmontou (ou o CEP mudou) no meio da consulta: libera o mesmo CEP
    // para ser consultado de novo, senão a tela ficaria em "Buscando…".
    return () => { if (vivo) consultado.current = ""; vivo = false; };
  }, [digitos, setForm]);

  return { ...estado, ruaRef };
}

function Localizacao({ dados, form, setForm }) {
  const site = dados.site;
  const mexer = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const estruturadoVazio = !site.endereco_rua && !site.cidade;
  const temCoords = site.latitude != null && site.longitude != null;
  const tirando = temCoords && form.coordenadas_mexeu && !form.coordenadas.trim();
  const posicao = temCoords ? `${site.latitude}, ${site.longitude}` : "";
  const cepAviso = useCepAutomatico(form, setForm);

  return (
    <Bloco icone={MapPin} titulo="Onde fica a agência" testid="site-local"
      hint="Endereço completo e a posição no mapa: é o que faz a agência aparecer na busca local do Google (“agência de passeios em Barreirinhas”).">
      <div className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-[160px_2fr_100px] gap-4">
          <Campo label="CEP" hint={cepAviso.texto}>
            <input className="zz-input tabular-nums" inputMode="numeric" value={form.cep} maxLength={9}
              data-testid="site-cep" placeholder="65590-000" aria-busy={cepAviso.buscando}
              onChange={(e) => setForm((f) => ({ ...f, cep: mascaraCep(e.target.value) }))} />
          </Campo>
          <Campo label="Cidade">
            <input className="zz-input" value={form.cidade} maxLength={MAX_CIDADE} data-testid="site-cidade"
              placeholder="Barreirinhas" onChange={mexer("cidade")} />
          </Campo>
          <Campo label="UF">
            <select className="zz-input" value={form.uf} data-testid="site-uf" onChange={mexer("uf")}>
              <option value="">—</option>
              {UFS.map((uf) => <option key={uf} value={uf}>{uf}</option>)}
            </select>
          </Campo>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-[2fr_1fr] gap-4">
          <Campo label="Rua e número" contador={<Contador n={form.endereco_rua.length} max={MAX_RUA} />}>
            <input ref={cepAviso.ruaRef} className="zz-input" value={form.endereco_rua} maxLength={MAX_RUA}
              data-testid="site-rua" placeholder="Av. Brasília, 100" onChange={mexer("endereco_rua")} />
          </Campo>
          <Campo label="Bairro">
            <input className="zz-input" value={form.endereco_bairro} maxLength={MAX_BAIRRO} data-testid="site-bairro"
              placeholder="Centro" onChange={mexer("endereco_bairro")} />
          </Campo>
        </div>

        <Campo label="Localização no mapa"
          hint="Abra o local no Google Maps, copie o endereço da barra do navegador e cole aqui. Também aceita as coordenadas, como -2.7477, -42.8269.">
          <input className="zz-input tabular-nums" value={form.coordenadas} data-testid="site-coordenadas"
            spellCheck={false} autoCapitalize="off"
            placeholder={temCoords ? "Cole um link novo para trocar a localização" : "https://www.google.com/maps/place/…"}
            onChange={(e) => setForm((f) => ({ ...f, coordenadas: e.target.value, coordenadas_mexeu: true }))} />
        </Campo>
        {temCoords && !tirando && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2" data-testid="site-coordenadas-marcada">
            <CheckCircle size={14} weight="regular" className="text-success" />
            <span>Localização marcada (<span className="tabular-nums">{posicao}</span>)</span>
            <a className="text-brand underline" target="_blank" rel="noopener noreferrer"
              data-testid="site-coordenadas-conferir"
              href={`https://www.google.com/maps/search/?api=1&query=${site.latitude},${site.longitude}`}>
              Conferir no mapa
            </a>
            <button type="button" className="zz-btn zz-btn-secondary text-xs py-1" data-testid="site-coordenadas-tirar"
              onClick={() => setForm((f) => ({ ...f, coordenadas: "", coordenadas_mexeu: true }))}>
              <Trash size={13} weight="regular" /> Tirar localização
            </button>
          </div>
        )}
        {tirando && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-warning" data-testid="site-coordenadas-tirando">
            A localização será tirada ao salvar.
            <button type="button" className="underline text-brand" data-testid="site-coordenadas-manter"
              onClick={() => setForm((f) => ({ ...f, coordenadas: "", coordenadas_mexeu: false }))}>
              Manter
            </button>
          </div>
        )}

        {estruturadoVazio && <NotaTextoAntigo texto={site.endereco} testid="site-endereco-antigo" />}
        <ComoAparece texto={dados.endereco_exibido} testid="site-endereco-exibido" />
      </div>
    </Bloco>
  );
}

function EditorHorarios({ itens, onChange }) {
  const lista = itens || [];
  const mexerItem = (i, parte) => onChange(lista.map((h, j) => (j === i ? { ...h, ...parte } : h)));
  const alternarDia = (i, d) => {
    const dias = lista[i].dias || [];
    mexerItem(i, { dias: dias.includes(d) ? dias.filter((x) => x !== d) : [...dias, d].sort((a, b) => a - b) });
  };
  const adicionar = (base) => onChange([...lista, base || { dias: [], abre: "", fecha: "" }]);

  return (
    <div className="space-y-2" data-testid="site-horarios">
      {lista.map((h, i) => (
        <div key={i} className="border border-line rounded-lg p-3 flex flex-wrap items-center gap-3"
          data-testid={`site-horario-faixa-${i}`}>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Dias">
            {DIAS_CURTOS.map((nome, d) => {
              const ligado = (h.dias || []).includes(d);
              return (
                <button key={nome} type="button" aria-pressed={ligado}
                  data-testid={`site-horario-dia-${i}-${d}`}
                  onClick={() => alternarDia(i, d)}
                  className={`text-xs px-2.5 py-1.5 rounded-md border min-w-[42px] ${
                    ligado ? "border-info ring-1 ring-info bg-brand-soft text-brand font-medium" : "border-line text-ink-2 hover:bg-surface-2"}`}>
                  {nome}
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-2 text-sm text-ink-2">
            <span>das</span>
            <CampoHora valor={h.abre} testid={`site-horario-abre-${i}`} onChange={(v) => mexerItem(i, { abre: v })} />
            <span>às</span>
            <CampoHora valor={h.fecha} testid={`site-horario-fecha-${i}`} onChange={(v) => mexerItem(i, { fecha: v })} />
          </div>
          <button type="button" className="zz-btn-icon ml-auto" aria-label="Remover faixa" title="Remover faixa"
            data-testid={`site-horario-remover-${i}`}
            onClick={() => onChange(lista.filter((_, j) => j !== i))}>
            <Trash size={14} weight="regular" />
          </button>
        </div>
      ))}
      {lista.length < MAX_HORARIOS && (
        <div className="flex flex-wrap gap-2">
          <button type="button" className="zz-btn zz-btn-secondary text-xs py-1" data-testid="site-horario-adicionar"
            onClick={() => adicionar()}>
            <Plus size={13} weight="regular" /> Adicionar horário
          </button>
          {lista.length === 0 && (
            <button type="button" className="zz-btn zz-btn-secondary text-xs py-1" data-testid="site-horario-seg-sex"
              onClick={() => adicionar({ dias: [0, 1, 2, 3, 4], abre: "08:00", fecha: "18:00" })}>
              Seg a sex, 08:00 às 18:00
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Confiança, avaliações, perguntas e políticas (fase 3.1). Cada seção só
// aparece no site quando tem alguma coisa preenchida.
// ---------------------------------------------------------------------------

const SO_QUANDO = "Só aparece no site quando preenchida.";

function Confianca({ form, setForm }) {
  return (
    <Bloco icone={ShieldCheck} titulo="Confiança" testid="site-confianca"
      hint={`O que mostra ao turista que a agência é séria. ${SO_QUANDO}`}>
      <div className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Campo label="Número do CADASTUR" hint="O registro da agência no Ministério do Turismo.">
            <input className="zz-input tabular-nums" value={form.cadastur} maxLength={MAX_CADASTUR}
              data-testid="site-cadastur" placeholder="21.123456.10.0001-1"
              onChange={(e) => setForm((f) => ({ ...f, cadastur: e.target.value }))} />
          </Campo>
          <Campo label="Ano de fundação" hint="Vira “Desde 2012” no site.">
            <input className="zz-input tabular-nums" inputMode="numeric" maxLength={4} value={form.desde}
              data-testid="site-desde" placeholder="2012"
              onChange={(e) => setForm((f) => ({ ...f, desde: e.target.value.replace(/\D/g, "") }))} />
          </Campo>
        </div>
        <Campo label="Diferenciais" hint="Frases curtas, como “Frota 4x4 própria”.">
          <ListaItens itens={form.diferenciais} testid="site-diferenciais" placeholder="Frota 4x4 própria"
            max={MAX_DIFERENCIAIS} tamanho={MAX_DIFERENCIAL}
            onChange={(l) => setForm((f) => ({ ...f, diferenciais: l }))} />
        </Campo>
      </div>
    </Bloco>
  );
}

function Avaliacoes({ form, setForm }) {
  const mexer = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  return (
    <Bloco icone={Star} titulo="Avaliações e depoimentos" testid="site-avaliacoes"
      hint={`A nota da agência no Google ou no TripAdvisor e o que os turistas disseram. ${SO_QUANDO}`}>
      <div className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Campo label="Nota" hint="De 1 a 5, como 4,8.">
            <input className="zz-input tabular-nums" inputMode="decimal" maxLength={3}
              value={form.nota_avaliacoes} data-testid="site-nota-avaliacoes" placeholder="4,8"
              onChange={(e) => setForm((f) => ({ ...f, nota_avaliacoes: e.target.value.replace(/[^\d,.]/g, "") }))} />
          </Campo>
          <Campo label="Total de avaliações">
            <input className="zz-input tabular-nums" inputMode="numeric" value={form.total_avaliacoes}
              data-testid="site-total-avaliacoes" placeholder="320"
              onChange={(e) => setForm((f) => ({ ...f, total_avaliacoes: e.target.value.replace(/\D/g, "") }))} />
          </Campo>
          <Campo label="Onde" contador={<Contador n={form.fonte_avaliacoes.length} max={MAX_FONTE} />}
            hint="Em branco, o site diz Google.">
            <input className="zz-input" value={form.fonte_avaliacoes} maxLength={MAX_FONTE}
              data-testid="site-fonte-avaliacoes" placeholder="Google" onChange={mexer("fonte_avaliacoes")} />
          </Campo>
        </div>
        <Campo label="Link das avaliações" hint="A página da agência no Google ou no TripAdvisor, começando com https://.">
          <input className="zz-input" value={form.link_avaliacoes} maxLength={MAX_LINK} spellCheck={false}
            autoCapitalize="off" data-testid="site-link-avaliacoes" placeholder="https://g.page/r/…"
            onChange={mexer("link_avaliacoes")} />
        </Campo>
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">Depoimentos</label>
          <ListaObjetos itens={form.depoimentos} max={MAX_DEPOIMENTOS} testid="site-depoimento"
            rotuloAdicionar="Adicionar depoimento"
            novo={() => ({ nome: "", origem: "", texto: "", nota: null })}
            onChange={(l) => setForm((f) => ({ ...f, depoimentos: l }))}
            renderItem={(d, i, mexerItem) => (
              <>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <input className="zz-input" value={d.nome} maxLength={MAX_DEP_NOME} placeholder="Nome"
                    aria-label="Nome" data-testid={`site-depoimento-nome-${i}`}
                    onChange={(e) => mexerItem({ nome: e.target.value })} />
                  <input className="zz-input" value={d.origem} maxLength={MAX_DEP_ORIGEM}
                    placeholder="De onde (opcional): São Paulo, SP" aria-label="De onde"
                    data-testid={`site-depoimento-origem-${i}`}
                    onChange={(e) => mexerItem({ origem: e.target.value })} />
                  <select className="zz-input" value={d.nota == null ? "" : String(d.nota)} aria-label="Nota"
                    data-testid={`site-depoimento-nota-${i}`}
                    onChange={(e) => mexerItem({ nota: e.target.value === "" ? null : Number(e.target.value) })}>
                    <option value="">Sem nota</option>
                    {[5, 4, 3, 2, 1].map((n) => (
                      <option key={n} value={n}>{n} {n === 1 ? "estrela" : "estrelas"}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <textarea className="zz-input min-h-[70px]" value={d.texto} maxLength={MAX_DEP_TEXTO}
                    placeholder="O que a pessoa disse" aria-label="Depoimento"
                    data-testid={`site-depoimento-texto-${i}`}
                    onChange={(e) => mexerItem({ texto: e.target.value })} />
                  <div className="text-right"><Contador n={d.texto.length} max={MAX_DEP_TEXTO} /></div>
                </div>
              </>
            )} />
        </div>
      </div>
    </Bloco>
  );
}

function Perguntas({ form, setForm }) {
  return (
    <Bloco icone={Question} titulo="Perguntas frequentes" testid="site-perguntas"
      hint={`As dúvidas que todo turista manda no WhatsApp, respondidas na página inicial. As de um passeio só ficam na página dele. ${SO_QUANDO}`}>
      <EditorPerguntas itens={form.faq} max={MAX_PERGUNTAS_SITE} testid="site-faq"
        onChange={(l) => setForm((f) => ({ ...f, faq: l }))} />
    </Bloco>
  );
}

function Politicas({ dados, form, setForm }) {
  const site = dados.site;
  return (
    <Bloco icone={Clock} titulo="Atendimento e políticas" testid="site-politicas"
      hint={`Quando a agência atende, como recebe e o que acontece se o turista desistir ou chover. ${SO_QUANDO}`}>
      <div className="space-y-4">
        <Campo label="Horário de atendimento"
          hint="Uma faixa para cada horário diferente: por exemplo, Seg a sex das 07:00 às 20:00 e Sáb das 08:00 às 12:00.">
          <div className="space-y-2">
            <EditorHorarios itens={form.horarios} onChange={(l) => setForm((f) => ({ ...f, horarios: l }))} />
            {!(site.horarios || []).length && (
              <NotaTextoAntigo texto={site.horario} oque="o horário" testid="site-horario-antigo" />
            )}
            <ComoAparece texto={dados.horario_exibido} testid="site-horario-exibido" />
          </div>
        </Campo>
        <Campo label="Formas de pagamento" hint={`Até ${MAX_PAGAMENTO}.`}>
          <ListaItens itens={form.pagamento} testid="site-pagamento" placeholder="Pix"
            max={MAX_PAGAMENTO} tamanho={MAX_PAGAMENTO_ITEM}
            onChange={(l) => setForm((f) => ({ ...f, pagamento: l }))} />
        </Campo>
        <Campo label="Política de cancelamento" contador={<Contador n={form.cancelamento.length} max={MAX_CANCELAMENTO} />}
          hint="Inclua o que acontece quando a saída é cancelada por chuva.">
          <textarea className="zz-input min-h-[100px]" value={form.cancelamento} maxLength={MAX_CANCELAMENTO}
            data-testid="site-cancelamento"
            placeholder="Cancelamento grátis até 24h antes. Saída cancelada por chuva: remarcamos ou devolvemos o valor."
            onChange={(e) => setForm((f) => ({ ...f, cancelamento: e.target.value }))} />
        </Campo>
        <label className="flex items-start gap-2 text-sm cursor-pointer">
          <input type="checkbox" className="mt-0.5" checked={form.mostrar_embarque !== false}
            data-testid="site-mostrar-embarque"
            onChange={(e) => setForm((f) => ({ ...f, mostrar_embarque: e.target.checked }))} />
          <span>
            Mostrar “Buscamos você na hospedagem”
            <span className="block text-[11px] text-ink-3">
              Lista no site os pontos de embarque já cadastrados em{" "}
              <Link to="/app/reservas-embarque" className="text-brand underline" data-testid="site-link-pontos">
                Reservas e embarque
              </Link>
              , com a antecedência da busca. Sem nenhum ponto ativo, a seção não aparece.
            </span>
          </span>
        </label>
      </div>
    </Bloco>
  );
}

function DominioProprio({ dados, form, setForm }) {
  const [aberto, setAberto] = useState(!!dados.site.dominio_proprio);
  const alvo = `${slugFinal(form.slug) || dados.site.slug || "seu-endereco"}.${dados.dominio_base}`;
  return (
    <section className="border border-line bg-surface rounded-xl mb-4" data-testid="site-dominio">
      <button type="button" className="w-full flex items-center justify-between gap-3 p-6 text-left"
        data-testid="site-dominio-abrir" onClick={() => setAberto((v) => !v)} aria-expanded={aberto}>
        <span className="flex items-center gap-2">
          <MapPin size={16} weight="regular" className="text-ink-3" />
          <span className="font-heading text-lg">Domínio próprio</span>
          {dados.site.dominio_proprio && (
            <span className="text-xs text-ink-3 tabular-nums">{dados.site.dominio_proprio}</span>
          )}
          {dados.site.dominio_proprio && (
            dados.dominio_ativo ? (
              <span className="text-xs font-medium text-success inline-flex items-center gap-1" data-testid="site-dominio-ativo">
                <CheckCircle size={12} weight="regular" /> Ativo
              </span>
            ) : (
              <span className="text-xs font-medium text-warning" data-testid="site-dominio-aguardando">Aguardando o DNS</span>
            )
          )}
        </span>
        {aberto ? <CaretDown size={16} /> : <CaretRight size={16} />}
      </button>
      {aberto && (
        <div className="px-6 pb-6 space-y-4">
          <p className="text-xs text-ink-3">
            Opcional. Para o site abrir também num endereço da agência, como www.suaagencia.com.br.
          </p>
          <Campo label="Endereço">
            <input className="zz-input tabular-nums" value={form.dominio_proprio} data-testid="site-dominio-campo"
              placeholder="www.suaagencia.com.br" spellCheck={false} autoCapitalize="off"
              onChange={(e) => setForm((f) => ({ ...f, dominio_proprio: e.target.value }))}
              onBlur={() => setForm((f) => ({ ...f, dominio_proprio: limpaDominio(f.dominio_proprio) }))} />
          </Campo>
          <div className="border border-line rounded-lg p-4 bg-surface-2 text-sm text-ink-2 space-y-2"
            data-testid="site-dominio-instrucao">
            <div className="font-medium text-ink">Como apontar</div>
            <p>
              No painel de onde o domínio foi registrado (Registro.br, GoDaddy, Hostinger…),
              aponte um registro <strong>CNAME</strong> de <code className="tabular-nums">www</code> para{" "}
              <code className="tabular-nums break-all">{alvo}</code>.
            </p>
            <p className="text-xs text-ink-3">
              Use o endereço com www: o domínio sem www não aceita CNAME na maioria dos provedores.
              O certificado de segurança (o cadeado do https) é emitido automaticamente no primeiro
              acesso depois que o DNS propagar, o que pode levar algumas horas.
            </p>
          </div>
          {dados.site.dominio_proprio && (
            <p className="text-xs text-ink-2" data-testid="site-dominio-estado">
              {dados.dominio_ativo
                ? `Ativo: ${dados.site.dominio_proprio} é o endereço oficial do site. Quem abrir o endereço antigo é levado para ele, e o Google indexa só este.`
                : "Assim que o primeiro visitante chegar pelo domínio, ele vira o endereço oficial e o endereço antigo passa a levar para ele. Até lá, o site continua no endereço antigo, para não sair do ar enquanto o DNS propaga."}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Google (Search Console)
// ---------------------------------------------------------------------------

/** O caminho para a agência ver o site no Google: provar que é dona e mandar o
 * sitemap. O código fica guardado; o site publica a tag sozinho. */
function Google({ dados, form, setForm }) {
  const site = dados.url_canonica;
  return (
    <Bloco icone={MagnifyingGlass} titulo="Google" testid="site-google"
      hint="Para acompanhar o site na busca do Google: quantas pessoas acharam, por quais palavras, e se alguma página tem problema.">
      <div className="space-y-4">
        <ol className="list-decimal pl-5 text-sm text-ink-2 space-y-1.5">
          <li>
            Abra o{" "}
            <a className="text-brand underline" href="https://search.google.com/search-console" target="_blank"
              rel="noopener noreferrer">Google Search Console</a>{" "}
            com a conta Google da agência e adicione uma propriedade do tipo <strong>Prefixo do URL</strong>{site ? (
              <> com o endereço <code className="tabular-nums break-all">{site}</code></>
            ) : null}.
          </li>
          <li>Na verificação, escolha <strong>Tag HTML</strong>, copie a tag e cole aqui embaixo. Salve.</li>
          <li>Volte ao Search Console e clique em <strong>Verificar</strong>.</li>
          <li>Em <strong>Sitemaps</strong>, envie o endereço do sitemap abaixo.</li>
        </ol>
        <Campo label="Tag de verificação"
          hint="Pode colar a tag inteira (<meta name=&quot;google-site-verification&quot; …>) ou só o código.">
          <input className="zz-input tabular-nums" value={form.google_verificacao} data-testid="site-google-verificacao"
            placeholder='<meta name="google-site-verification" content="…" />' spellCheck={false} autoCapitalize="off"
            onChange={(e) => setForm((f) => ({ ...f, google_verificacao: e.target.value }))} />
        </Campo>
        {dados.url_sitemap && (
          <Campo label="Endereço do sitemap">
            <div className="flex gap-2">
              <input className="zz-input tabular-nums" readOnly value={dados.url_sitemap} data-testid="site-sitemap" />
              <button type="button" className="zz-btn zz-btn-secondary text-xs shrink-0" data-testid="site-sitemap-copiar"
                onClick={() => copiar(dados.url_sitemap)}>
                <Copy size={13} weight="regular" /> Copiar
              </button>
            </div>
          </Campo>
        )}
        {dados.site.dominio_proprio && !dados.dominio_ativo && (
          <p className="text-xs text-warning" data-testid="site-google-aviso-dominio">
            Com domínio próprio, faça isto depois que ele estiver ativo: o Google deve conhecer o endereço oficial.
          </p>
        )}
      </div>
    </Bloco>
  );
}

// ---------------------------------------------------------------------------
// Idiomas (inglês e espanhol)
// ---------------------------------------------------------------------------

/** Uma linha da revisão: o original em português e a tradução editável, com
 * salvar próprio. Componente à parte para cada item ter a sua trava. */
function ItemTraducao({ item, i, idioma, onSalvo }) {
  const [texto, setTexto] = useState(item.traducao || "");
  useEffect(() => setTexto(item.traducao || ""), [item.traducao]);
  const mudou = texto.trim() !== (item.traducao || "").trim();

  const [salvar, salvando] = useAcao(async () => {
    try {
      const r = await api.put("/site/traducoes", { idioma, chave: item.chave, texto: texto.trim() });
      onSalvo({ ...item, traducao: r.data?.traducao ?? null, manual: !!r.data?.manual });
      toast.success(r.data?.traducao ? "Tradução salva" : "Tradução apagada: o site mostra o português");
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  return (
    <div className="border border-line rounded-lg p-3 space-y-2" data-testid={`traducao-item-${i}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] text-ink-3">{item.rotulo}</span>
        {item.manual ? (
          <span className="text-[11px] font-medium text-brand" data-testid={`traducao-manual-${i}`}>Corrigida à mão</span>
        ) : !item.traducao ? (
          <span className="text-[11px] font-medium text-warning">Sem tradução: aparece em português</span>
        ) : null}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div className="text-sm text-ink-2 bg-surface-2 border border-line rounded-lg p-2.5 whitespace-pre-wrap break-words">
          {item.texto}
        </div>
        <textarea className="zz-input min-h-[70px]" value={texto} data-testid={`traducao-texto-${i}`}
          aria-label={`Tradução: ${item.rotulo}`} placeholder="Sem tradução"
          onChange={(e) => setTexto(e.target.value)} />
      </div>
      <div className="flex justify-end">
        <button type="button" className="zz-btn zz-btn-secondary text-xs py-1" data-testid={`traducao-salvar-${i}`}
          disabled={salvando || !mudou} onClick={() => salvar()}>
          {salvando ? "Salvando…" : "Salvar"}
        </button>
      </div>
    </div>
  );
}

function RevisarTraducoes({ idioma, onFechar }) {
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState(null);
  const [soFaltando, setSoFaltando] = useState(false);

  useEffect(() => {
    let vivo = true;
    api.get("/site/traducoes", { params: { idioma: idioma.id } })
      .then((r) => { if (vivo) setDados(r.data); })
      .catch((e) => { if (vivo) setErro(formatApiErrorDetail(e.response?.data?.detail)); });
    return () => { vivo = false; };
  }, [idioma.id]);

  const itens = dados?.itens || [];
  const traduzidos = itens.filter((x) => x.traducao).length;
  const aoSalvar = (novo) => setDados((d) => ({
    ...d, itens: d.itens.map((x) => (x.chave === novo.chave ? novo : x)),
  }));

  return (
    <Modal titulo={`Revisar tradução: ${idioma.nome}`} largo testId={`site-revisar-modal-${idioma.id}`}
      descricao="À esquerda o texto em português, à direita a tradução. Apague a tradução para o site voltar a mostrar o português daquele texto."
      onFechar={onFechar}>
      {erro && <div className="border border-danger bg-danger-soft text-danger rounded-xl p-3 text-sm">{erro}</div>}
      {!dados && !erro && <p className="text-sm text-ink-3">Carregando…</p>}
      {dados && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm text-ink-2 tabular-nums">{traduzidos} de {itens.length} textos traduzidos</span>
            <label className="flex items-center gap-2 text-sm text-ink-2 cursor-pointer">
              <input type="checkbox" checked={soFaltando} data-testid="traducao-so-faltando"
                onChange={(e) => setSoFaltando(e.target.checked)} />
              Só os que faltam
            </label>
          </div>
          {itens.length === 0 && <p className="text-sm text-ink-3">O site ainda não tem texto para traduzir.</p>}
          {itens.map((item, i) => (soFaltando && item.traducao ? null : (
            <ItemTraducao key={item.chave} item={item} i={i} idioma={idioma.id} onSalvo={aoSalvar} />
          )))}
          {soFaltando && itens.length > 0 && traduzidos === itens.length && (
            <p className="text-sm text-ink-3">Tudo traduzido.</p>
          )}
        </>
      )}
    </Modal>
  );
}

function Idiomas({ dados, onAtualizar }) {
  const ligados = dados.site.idiomas || [];
  const publicado = !!dados.site.publicado;
  const [progresso, setProgresso] = useState(null); // { idioma, feitos, total }
  const [revisando, setRevisando] = useState(null);

  const [alternar, alternando] = useAcao(async (id, ligar) => {
    const lista = IDIOMAS.map((x) => x.id).filter((x) => (x === id ? ligar : ligados.includes(x)));
    try {
      await api.put("/site", { idiomas: lista });
      toast.success(ligar ? "Idioma ligado" : "Idioma desligado");
      await onAtualizar();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  // Um lote por pedido: a tela chama de novo enquanto sobrar texto, e mostra
  // o andamento. Erro (sem chave de IA, IA respondendo mal) para o laço.
  const [traduzir, traduzindo] = useAcao(async (id) => {
    const resumo = dados.traducoes?.[id] || {};
    let feitos = resumo.traduzidos || 0;
    const total = resumo.total || 0;
    setProgresso({ idioma: id, feitos, total });
    try {
      for (;;) {
        const r = await api.post("/site/traduzir", { idioma: id });
        const n = r.data?.traduzidos || 0;
        const restantes = r.data?.restantes || 0;
        feitos += n;
        setProgresso({ idioma: id, feitos, total: Math.max(total, feitos + restantes) });
        // Lote sem avanço com texto sobrando pararia o laço para sempre.
        if (restantes <= 0 || n <= 0) break;
      }
      toast.success("Tradução concluída");
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    } finally {
      setProgresso(null);
      await onAtualizar();
    }
  });

  return (
    <Bloco icone={Translate} titulo="Idiomas" testid="site-idiomas"
      hint="O site ganha versões em /en/ e /es/, e o Google mostra a versão certa a quem busca em cada língua. Texto sem tradução aparece em português.">
      <div className="space-y-3">
        {IDIOMAS.map((idi) => {
          const ligado = ligados.includes(idi.id);
          const r = dados.traducoes?.[idi.id] || { total: 0, traduzidos: 0, faltando: 0 };
          const meu = progresso?.idioma === idi.id;
          return (
            <div key={idi.id} className="border border-line rounded-lg p-4 flex flex-wrap items-center gap-3"
              data-testid={`site-idioma-linha-${idi.id}`}>
              <label className="flex items-center gap-2 text-sm font-medium cursor-pointer min-w-[120px]">
                <input type="checkbox" checked={ligado} disabled={alternando} data-testid={`site-idioma-${idi.id}`}
                  onChange={(e) => alternar(idi.id, e.target.checked)} />
                {idi.nome}
              </label>
              <span className="text-xs text-ink-2 tabular-nums flex-1 min-w-[160px]" data-testid={`site-idioma-resumo-${idi.id}`}>
                {meu
                  ? `Traduzindo… ${progresso.feitos} de ${progresso.total}`
                  : `${r.traduzidos} de ${r.total} textos traduzidos`}
              </span>
              <div className="flex flex-wrap items-center gap-2">
                {publicado && ligado && dados.url && (
                  <a href={`${dados.url}/${idi.id}/`} target="_blank" rel="noopener noreferrer"
                    data-testid={`site-ver-${idi.id}`}
                    className="text-xs text-brand font-medium inline-flex items-center gap-1 hover:underline">
                    {idi.ver} <ArrowSquareOut size={12} weight="regular" />
                  </a>
                )}
                <button type="button" className="zz-btn zz-btn-secondary text-xs py-1" data-testid={`site-traduzir-${idi.id}`}
                  disabled={traduzindo || (r.total > 0 && r.faltando === 0)} onClick={() => traduzir(idi.id)}>
                  <Sparkle size={13} weight="regular" /> {meu ? "Traduzindo…" : "Traduzir com IA"}
                </button>
                <button type="button" className="zz-btn zz-btn-secondary text-xs py-1" data-testid={`site-revisar-${idi.id}`}
                  disabled={traduzindo} onClick={() => setRevisando(idi)}>
                  <PencilSimple size={13} weight="regular" /> Revisar
                </button>
              </div>
            </div>
          );
        })}
        <p className="text-[11px] text-ink-3">
          A tradução usa a chave de IA do assistente de IA da agência (ou a da plataforma) e custa centavos.
          Ao mudar um texto em português, só ele volta a precisar de tradução, e até lá aparece em português.
        </p>
      </div>
      {revisando && (
        <RevisarTraducoes idioma={revisando} onFechar={() => { setRevisando(null); onAtualizar(); }} />
      )}
    </Bloco>
  );
}

// ---------------------------------------------------------------------------
// Guias (a tela é outra; aqui só o atalho)
// ---------------------------------------------------------------------------

function Guias() {
  return (
    <Bloco icone={BookOpenText} titulo="Guias" testid="site-guias"
      hint="Páginas como “Melhor época para os Lençóis” e “Como chegar a Barreirinhas”, que o Google e as IAs citam.">
      <Link to="/app/site/guias" className="zz-btn zz-btn-secondary" data-testid="site-guias-link">
        Abrir os guias <CaretRight size={14} weight="regular" />
      </Link>
    </Bloco>
  );
}

// ---------------------------------------------------------------------------
// Resultados
// ---------------------------------------------------------------------------

function Numero({ rotulo, valor, testid, nota }) {
  return (
    <div className="border border-line rounded-lg p-4" data-testid={testid}>
      <div className="text-xs text-ink-3 font-medium">{rotulo}</div>
      <div className="font-heading text-3xl mt-1 tabular-nums">{valor ?? "—"}</div>
      {nota && <div className="text-[11px] text-ink-3 mt-0.5">{nota}</div>}
    </div>
  );
}

function Resultados({ metricas }) {
  const m = metricas || {};
  const cliques = m.cliques || 0;
  const conversas = m.conversas || 0;
  const linhas = m.por_passeio || [];
  return (
    <Bloco icone={ChartBar} titulo={`Resultados (${m.dias || 30} dias)`} testid="site-resultados"
      hint="Quem tocou no botão do WhatsApp no site e quantos chegaram de fato a mandar a mensagem com o código.">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Numero rotulo="Cliques no WhatsApp" valor={cliques} testid="site-metrica-cliques" />
        <Numero rotulo="Conversas com o código" valor={conversas} testid="site-metrica-conversas" />
        <Numero rotulo="Orçamentos abertos" valor={m.orcamentos || 0} testid="site-metrica-orcamentos" />
        <Numero rotulo="Cliques que viraram conversa" valor={porcento(conversas, cliques)}
          testid="site-metrica-taxa" nota={cliques ? null : "sem cliques ainda"} />
      </div>
      {linhas.length > 0 ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm" data-testid="site-metricas-passeio">
            <thead>
              <tr className="text-left text-xs text-ink-3 border-b border-line">
                <th className="py-2 pr-3 font-medium">Passeio</th>
                <th className="py-2 px-3 font-medium text-right">Cliques</th>
                <th className="py-2 px-3 font-medium text-right">Conversas</th>
                <th className="py-2 pl-3 font-medium text-right">Taxa</th>
              </tr>
            </thead>
            <tbody>
              {linhas.map((l) => (
                <tr key={l.service_id || l.nome} className="border-b border-line last:border-0">
                  <td className="py-2 pr-3">{l.nome}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{l.cliques || 0}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{l.conversas || 0}</td>
                  <td className="py-2 pl-3 text-right tabular-nums">{porcento(l.conversas || 0, l.cliques || 0) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-ink-3 mt-4">Nenhum clique em passeio no período.</p>
      )}
      <p className="text-xs mt-4">
        <Link to="/app/desempenho-site" className="text-brand underline" data-testid="site-ver-desempenho">
          Ver visitas, origens e reservas no Desempenho do site
        </Link>
      </p>
    </Bloco>
  );
}

// ---------------------------------------------------------------------------
// Passeios no site
// ---------------------------------------------------------------------------

function comCamposSite(s) {
  return {
    ...s,
    site_visivel: s.site_visivel !== false,
    site_resumo: s.site_resumo || "",
    site_descricao: s.site_descricao || "",
    site_inclui: s.site_inclui || [],
    site_levar: s.site_levar || [],
    site_fotos: s.site_fotos || [],
    site_slug: s.site_slug || "",
  };
}

function PasseiosNoSite({ servicos, onMudou, urlSite, publicado }) {
  const [editando, setEditando] = useState(null);
  const ativos = servicos.filter((s) => s.ativo !== false);
  const inativos = servicos.length - ativos.length;

  const [alternar, alternando] = useAcao(async (s, visivel) => {
    try {
      await api.patch(`/agenda/services/${s.id}`, corpoServico({ ...s, site_visivel: visivel }));
      onMudou();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const salvar = async () => {
    const erro = problemaSite(editando);
    if (erro) { toast.error(erro); return; }
    try {
      await api.patch(`/agenda/services/${editando.id}`, corpoServico(editando));
      setEditando(null);
      toast.success("Página do passeio salva");
      onMudou();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  return (
    <Bloco icone={MapPin} titulo="Passeios no site" testid="site-passeios"
      hint="Os passeios ativos. Preço e horários saem do cadastro do passeio; aqui fica o texto, as fotos e se ele aparece.">
      {ativos.length === 0 ? (
        <div className="border border-dashed border-line rounded-xl p-8 text-center text-sm text-ink-3">
          Nenhum passeio ativo.{" "}
          <Link to="/app/passeios" className="text-brand underline">Cadastrar passeio</Link>
        </div>
      ) : (
        <div className="divide-y divide-line border border-line rounded-lg">
          {ativos.map((s) => {
            const visivel = s.site_visivel !== false;
            const capa = (s.site_fotos || [])[0];
            return (
              <div key={s.id} className="flex flex-wrap sm:flex-nowrap items-center gap-3 p-3"
                data-testid="site-passeio">
                <div className="w-16 h-12 rounded-md bg-surface-2 border border-line overflow-hidden shrink-0 flex items-center justify-center">
                  {capa
                    ? <img src={urlMidia(capa)} alt="" className="w-full h-full object-cover" />
                    : <ImageSquare size={18} weight="regular" className="text-ink-4" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{s.nome}</div>
                  <div className={`text-xs truncate ${s.site_resumo ? "text-ink-3" : "text-warning"}`}>
                    {s.site_resumo || "Sem resumo: o cartão do site fica sem frase"}
                    {!capa && " · sem foto"}
                  </div>
                </div>
                <label className="flex items-center gap-2 text-xs text-ink-2 cursor-pointer shrink-0">
                  <input type="checkbox" checked={visivel} disabled={alternando}
                    data-testid={`site-passeio-visivel-${s.id}`}
                    onChange={(e) => alternar(s, e.target.checked)} />
                  Aparece no site
                </label>
                <div className="flex items-center gap-1 shrink-0">
                  {publicado && urlSite && s.site_slug && visivel && (
                    <a href={`${urlSite}/passeio/${s.site_slug}`} target="_blank" rel="noopener noreferrer"
                      className="zz-btn-icon" title="Ver no site" aria-label="Ver no site">
                      <ArrowSquareOut size={14} weight="regular" />
                    </a>
                  )}
                  <button type="button" className="zz-btn zz-btn-secondary text-xs py-1"
                    data-testid={`site-passeio-editar-${s.id}`} onClick={() => setEditando(comCamposSite(s))}>
                    <PencilSimple size={13} weight="regular" /> Editar página
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {inativos > 0 && (
        <p className="text-[11px] text-ink-3 mt-2">
          {inativos} passeio(s) desativado(s) não aparecem no site.
        </p>
      )}

      {editando && (
        <Modal titulo={`Página no site: ${editando.nome}`} largo testId="site-passeio-modal"
          descricao="Preço, horários e lugares continuam no cadastro do passeio, nas Saídas."
          onFechar={() => setEditando(null)} onSalvar={salvar}
          rodape={(
            <Link to="/app/passeios" className="zz-btn zz-btn-secondary mr-auto"
              data-testid="site-passeio-cadastro-completo">
              Cadastro completo
            </Link>
          )}>
          <CamposSitePasseio valor={editando} onChange={setEditando} />
        </Modal>
      )}
    </Bloco>
  );
}

// ---------------------------------------------------------------------------

export default function SitePage() {
  const [dados, setDados] = useState(null);
  const [form, setForm] = useState(null);
  const [servicos, setServicos] = useState([]);
  const [erro, setErro] = useState(null);

  const carregar = useCallback(async () => {
    try {
      const r = await api.get("/site");
      setDados(r.data);
      setForm(paraForm(r.data.site));
      setErro(null);
    } catch (e) {
      setErro(formatApiErrorDetail(e.response?.data?.detail));
    }
  }, []);

  // Recarrega o estado do site sem mexer no formulário: ligar um idioma ou
  // traduzir não pode jogar fora o que a pessoa digitou e ainda não salvou.
  const recarregarDados = useCallback(async () => {
    try {
      const r = await api.get("/site");
      setDados(r.data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  }, []);

  const carregarServicos = useCallback(() => {
    api.get("/agenda/services").then((r) => setServicos(r.data || [])).catch(() => {});
  }, []);

  useEffect(() => { carregar(); carregarServicos(); }, [carregar, carregarServicos]);

  // Os passeios entram em `pronto` (precisa de pelo menos um visível): mudar
  // um deles recarrega o estado do site também.
  const aoMudarPasseio = useCallback(() => { carregarServicos(); carregar(); }, [carregarServicos, carregar]);

  const sujo = useMemo(() => {
    if (!dados || !form) return false;
    return JSON.stringify(paraCorpo(form)) !== JSON.stringify(paraCorpo(paraForm(dados.site)));
  }, [dados, form]);

  const [salvar, salvando] = useAcao(async () => {
    const corpo = paraCorpo(form);
    const salvo = dados.site.slug || "";
    if (!corpo.slug && salvo) { toast.error("O endereço do site não pode ficar vazio"); return; }
    if (corpo.slug && (corpo.slug.length < 3 || !REGEX_SLUG.test(corpo.slug))) {
      toast.error("Endereço: de 3 a 40 letras minúsculas, números e hífen"); return;
    }
    const zap = form.whatsapp.replace(/\D/g, "");
    if (zap && zap.length !== 10 && zap.length !== 11) {
      toast.error("WhatsApp: informe o DDD e o número, como (98) 98111-2233"); return;
    }
    if (corpo.email && !REGEX_EMAIL.test(corpo.email)) { toast.error("E-mail inválido"); return; }
    if ((corpo.chamada || "").length > MAX_CHAMADA) { toast.error(`Chamada: até ${MAX_CHAMADA} caracteres`); return; }
    const problema = problemaForm(form);
    if (problema) { toast.error(problema); return; }
    if (dados.site.publicado && salvo && corpo.slug && corpo.slug !== salvo) {
      const ok = await confirmToast(
        `Trocar o endereço para "${corpo.slug}"? O endereço antigo deixa de funcionar e links já divulgados param.`,
        { confirmLabel: "Trocar endereço" },
      );
      if (!ok) return;
    }
    try {
      await api.put("/site", corpo);
      toast.success("Site salvo");
      await carregar();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const [publicar, publicando] = useAcao(async (valor) => {
    if (!valor && !(await confirmToast("Despublicar o site? O endereço deixa de abrir para o público até você publicar de novo.",
      { confirmLabel: "Despublicar" }))) return;
    try {
      await api.put("/site", { publicado: valor });
      toast.success(valor ? "Site publicado" : "Site despublicado");
      await carregar();
    } catch (e) {
      // 409 traz o que falta para publicar.
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
      await carregar();
    }
  });

  if (erro && !dados) {
    return (
      <div className="zz-page-form" data-testid="site-page">
        <h1 className="font-heading text-4xl font-semibold mb-4">Site</h1>
        <div className="border border-danger bg-danger-soft text-danger rounded-xl p-4 text-sm">{erro}</div>
      </div>
    );
  }
  if (!dados || !form) {
    return <div className="zz-page-form text-sm text-ink-3" data-testid="site-page">Carregando…</div>;
  }

  return (
    <div className="zz-page-form" data-testid="site-page">
      <Cabecalho dados={dados} sujo={sujo} onPublicar={publicar} publicando={publicando} />
      <Endereco dados={dados} form={form} setForm={setForm} />
      <Identidade form={form} setForm={setForm} />
      <Contato form={form} setForm={setForm} />
      <Localizacao dados={dados} form={form} setForm={setForm} />
      <Confianca form={form} setForm={setForm} />
      <Avaliacoes form={form} setForm={setForm} />
      <Perguntas form={form} setForm={setForm} />
      <Politicas dados={dados} form={form} setForm={setForm} />
      <Idiomas dados={dados} onAtualizar={recarregarDados} />
      <Guias />
      <DominioProprio dados={dados} form={form} setForm={setForm} />
      <Google dados={dados} form={form} setForm={setForm} />
      <Resultados metricas={dados.metricas} />
      <PasseiosNoSite servicos={servicos} onMudou={aoMudarPasseio} urlSite={dados.url}
        publicado={!!dados.site.publicado} />

      {sujo && (
        <div className="sticky bottom-0 z-10 -mx-2 mt-2" data-testid="site-barra-salvar">
          <div className="bg-surface border border-line rounded-xl shadow-pop px-4 py-3 flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm text-ink-2">Alterações não salvas</span>
            <div className="flex gap-2">
              <button type="button" className="zz-btn zz-btn-secondary" data-testid="site-descartar"
                disabled={salvando} onClick={() => setForm(paraForm(dados.site))}>
                Descartar
              </button>
              <button type="button" className="zz-btn" data-testid="site-salvar"
                disabled={salvando} onClick={() => salvar()}>
                {salvando ? "Salvando…" : "Salvar"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
