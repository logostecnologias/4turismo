import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft, ArrowSquareOut, BookOpenText, Eye, FileText, PencilSimple, Plus, Trash, Warning,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { confirmToast } from "@/lib/confirm-toast";
import { CampoComPrefixo, Contador, ImagemUnica } from "@/components/site/comum";

// Guias do site: páginas de conteúdo ("Melhor época para visitar os Lençóis",
// "Como chegar a Barreirinhas") em `<site>/guia/<slug>` e na lista `<site>/guias`.
// São o tipo de página que o Google e as IAs citam quando alguém pergunta sobre
// a região. O backend é `site_guias.py`; aqui mora só a edição.
//
// **O PATCH regrava o guia inteiro**: todo salvamento manda todos os campos
// (`corpoGuia`), senão um campo esquecido seria apagado.
//
// **A pré-visualização aplica a mesma regra de `site_guias.blocos`** (## e ###
// subtítulo, "- " item, linha em branco separa parágrafo). Divergir faria a tela
// mostrar uma coisa e o site outra.

const MAX_TITULO = 120;
const MAX_RESUMO = 200;
const MAX_CORPO = 20000;
const MAX_PASSEIOS = 6;
const MIN_PUBLICAR = 200;
const MAX_SLUG = 80;

/** Slug como o do backend (`sites.slugify`), até 80 caracteres. Não apara o
 * hífen do fim enquanto digita, para dar para chegar à palavra seguinte. */
function paraSlugGuia(texto) {
  return String(texto || "")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .replace(/-{2,}/g, "-")
    .slice(0, MAX_SLUG);
}

function contaPalavras(texto) {
  const m = String(texto || "").match(/[\p{L}\p{N}_]+/gu);
  return m ? m.length : 0;
}

/** Mesma conta do backend (`minutos_de_leitura`): palavras/200, no mínimo 1. */
function minutosDeLeitura(texto) {
  return Math.max(1, Math.round(contaPalavras(texto) / 200));
}

/** O corpo em blocos, pela mesma regra de `site_guias.blocos`. */
function blocos(corpo) {
  const saida = [];
  let paragrafo = [];
  let lista = [];
  const fechaParagrafo = () => {
    if (paragrafo.length) saida.push({ tipo: "p", texto: paragrafo.join(" ") });
    paragrafo = [];
  };
  const fechaLista = () => {
    if (lista.length) saida.push({ tipo: "lista", itens: lista });
    lista = [];
  };
  const fecha = () => { fechaParagrafo(); fechaLista(); };
  String(corpo || "").replace(/\r\n/g, "\n").split("\n").forEach((linha) => {
    const l = linha.trim();
    if (!l) fecha();
    else if (l.startsWith("### ")) { fecha(); saida.push({ tipo: "h3", texto: l.slice(4).trim() }); }
    else if (l.startsWith("## ")) { fecha(); saida.push({ tipo: "h2", texto: l.slice(3).trim() }); }
    else if (/^[-*•]\s+/.test(l)) { fechaParagrafo(); lista.push(l.replace(/^[-*•]\s+/, "")); }
    else { fechaLista(); paragrafo.push(l); }
  });
  fecha();
  return saida;
}

/** As frases de orientação dos modelos (as linhas que não são subtítulo nem
 * item), como `site_guias._instrucoes`. Servem para avisar antes do 400. */
function instrucoesDos(modelos) {
  const saida = [];
  (modelos || []).forEach((m) => {
    String(m.corpo || "").split("\n").forEach((linha) => {
      const l = linha.trim();
      if (l && !l.startsWith("#") && !l.startsWith("-")) saida.push(l);
    });
  });
  return saida;
}

/** Prévia de um rascunho: o token assinado vem em `url_previa` do `/api/site`
 * (`<url>/?previa=TOKEN`) e vale para qualquer página do site. */
function urlPreviaGuia(site, slug) {
  if (!site?.url || !site?.url_previa || !slug) return null;
  try {
    const token = new URL(site.url_previa).searchParams.get("previa");
    if (!token) return null;
    return `${site.url.replace(/\/+$/, "")}/guia/${slug}?previa=${encodeURIComponent(token)}`;
  } catch {
    return null;
  }
}

function vazio() {
  return { id: null, titulo: "", resumo: "", corpo: "", capa_id: null, publicada: false, passeios: [], slug: "", ordem: 0 };
}

function paraForm(g) {
  return {
    id: g.id || null,
    titulo: g.titulo || "",
    resumo: g.resumo || "",
    corpo: g.corpo || "",
    capa_id: g.capa_id || null,
    publicada: !!g.publicada,
    passeios: g.passeios || [],
    slug: g.slug || "",
    ordem: g.ordem ?? 0,
  };
}

/** O guia inteiro, como o `GuiaIn` espera. */
function corpoGuia(f) {
  return {
    titulo: f.titulo.trim(),
    resumo: f.resumo.trim() || null,
    corpo: f.corpo,
    capa_id: f.capa_id || null,
    publicada: !!f.publicada,
    passeios: (f.passeios || []).slice(0, MAX_PASSEIOS),
    // Vazio: o servidor gera do título (ou mantém o atual, na edição).
    slug: f.slug.replace(/-+$/, "") || null,
    ordem: Number(f.ordem) || 0,
  };
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

function Interruptor({ ligado, onChange, testid, rotulo }) {
  return (
    <button type="button" role="switch" aria-checked={ligado} aria-label={rotulo} data-testid={testid}
      onClick={() => onChange(!ligado)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors ${
        ligado ? "bg-brand border-brand" : "bg-surface-3 border-line-strong"}`}>
      <span className={`inline-block h-4 w-4 rounded-full shadow transition-transform ${
        ligado ? "translate-x-6 bg-brand-fg" : "translate-x-1 bg-surface"}`} />
    </button>
  );
}

function Status({ publicada, testid }) {
  return (
    <span data-testid={testid}
      className={`text-[11px] font-medium px-2 py-0.5 rounded-full border shrink-0 ${
        publicada ? "bg-success-soft text-success border-success" : "bg-surface-3 text-ink-2 border-line"}`}>
      {publicada ? "Publicado" : "Rascunho"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Pré-visualização do texto
// ---------------------------------------------------------------------------

function Previa({ titulo, resumo, corpo }) {
  const partes = useMemo(() => blocos(corpo), [corpo]);
  return (
    <div className="border border-line rounded-xl bg-surface p-5 text-ink" data-testid="guia-previa">
      <h1 className="font-heading text-2xl font-semibold break-words">{titulo || "Título do guia"}</h1>
      {resumo && <p className="text-sm text-ink-2 mt-2">{resumo}</p>}
      <p className="text-[11px] text-ink-3 mt-2">{minutosDeLeitura(corpo)} min de leitura</p>
      <div className="mt-4 space-y-3 text-sm leading-relaxed">
        {partes.length === 0 && <p className="text-ink-3">O texto aparece aqui enquanto você escreve.</p>}
        {partes.map((b, i) => {
          if (b.tipo === "h2") return <h2 key={i} className="font-heading text-lg font-semibold pt-2">{b.texto}</h2>;
          if (b.tipo === "h3") return <h3 key={i} className="font-heading text-base font-semibold pt-1">{b.texto}</h3>;
          if (b.tipo === "lista") {
            return (
              <ul key={i} className="list-disc pl-5 space-y-1">
                {b.itens.map((it, k) => <li key={k}>{it}</li>)}
              </ul>
            );
          }
          return <p key={i}>{b.texto}</p>;
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modelos
// ---------------------------------------------------------------------------

function Modelos({ modelos, onEscolher, onEmBranco, desabilitado }) {
  return (
    <div data-testid="guias-modelos">
      <p className="text-xs text-ink-3 mb-3 max-w-2xl">
        O modelo é só a estrutura: os subtítulos e frases de orientação dizendo o que vai em cada
        parte. Troque cada frase pelo que a sua equipe sabe da região. Um guia com a orientação
        ainda no texto não publica.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {modelos.map((m) => (
          <div key={m.id} className="border border-line rounded-xl bg-surface p-4 flex flex-col">
            <div className="font-medium text-sm text-ink">{m.titulo}</div>
            {m.resumo && <p className="text-xs text-ink-3 mt-1 flex-1">{m.resumo}</p>}
            <div className="mt-3">
              <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid={`guia-modelo-${m.id}`}
                disabled={desabilitado} onClick={() => onEscolher(m)}>
                <FileText size={13} weight="regular" /> Começar com este modelo
              </button>
            </div>
          </div>
        ))}
        <div className="border border-dashed border-line-strong rounded-xl bg-surface-2 p-4 flex flex-col justify-center items-start">
          <div className="font-medium text-sm text-ink">Em branco</div>
          <p className="text-xs text-ink-3 mt-1">Para um assunto que nenhum modelo cobre.</p>
          <button type="button" className="zz-btn zz-btn-secondary text-xs mt-3" data-testid="guia-modelo-branco"
            disabled={desabilitado} onClick={onEmBranco}>
            <Plus size={13} weight="regular" /> Começar em branco
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

function Editor({ inicial, original, servicos, instrucoes, site, onSalvo, onCancelar }) {
  const [f, setF] = useState(inicial);
  const mexer = (patch) => setF((x) => ({ ...x, ...patch }));
  const sujo = JSON.stringify(f) !== JSON.stringify(original);

  const ativos = servicos.filter((s) => s.ativo !== false || (f.passeios || []).includes(s.id));
  const nomeDoPasseio = useMemo(() => Object.fromEntries(servicos.map((s) => [s.id, s.nome])), [servicos]);
  const orfaos = (f.passeios || []).filter((id) => !nomeDoPasseio[id]);

  const sobra = useMemo(() => instrucoes.find((i) => f.corpo.includes(i)), [instrucoes, f.corpo]);
  const curto = f.corpo.trim().length < MIN_PUBLICAR;

  const alternaPasseio = (id) => {
    const atual = f.passeios || [];
    if (atual.includes(id)) mexer({ passeios: atual.filter((x) => x !== id) });
    else if (atual.length >= MAX_PASSEIOS) toast.error(`Até ${MAX_PASSEIOS} passeios relacionados por guia.`);
    else mexer({ passeios: [...atual, id] });
  };

  const [salvar, salvando] = useAcao(async () => {
    if (!f.titulo.trim()) { toast.error("Dê um título ao guia"); return; }
    const slug = f.slug.replace(/-+$/, "");
    if (slug && slug.length < 2) { toast.error("Endereço do guia: pelo menos 2 letras"); return; }
    try {
      const corpo = corpoGuia(f);
      const r = f.id
        ? await api.patch(`/site/paginas/${f.id}`, corpo)
        : await api.post("/site/paginas", corpo);
      toast.success(f.id ? "Guia salvo" : "Guia criado");
      onSalvo(r.data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail) || "Não foi possível salvar o guia");
    }
  });

  const cancelar = async () => {
    if (sujo && !(await confirmToast("Descartar as alterações deste guia?", { confirmLabel: "Descartar" }))) return;
    onCancelar();
  };

  const previa = f.id && original.slug ? urlPreviaGuia(site, original.slug) : null;

  return (
    <div className="zz-page" data-testid="guia-editor">
      <button type="button" className="text-sm text-brand inline-flex items-center gap-1 hover:underline mb-3"
        data-testid="guia-voltar" onClick={cancelar}>
        <ArrowLeft size={14} weight="regular" /> Guias do site
      </button>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <h1 className="font-heading text-3xl font-semibold">{f.id ? "Editar guia" : "Novo guia"}</h1>
        {previa && (
          <a href={previa} target="_blank" rel="noopener noreferrer" data-testid="guia-editor-previa"
            className="zz-btn zz-btn-secondary text-xs">
            <Eye size={13} weight="regular" /> Ver no site (versão salva)
          </a>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <div className="space-y-5 border border-line bg-surface rounded-xl p-6">
          <Campo label="Título" contador={<Contador n={f.titulo.length} max={MAX_TITULO} />}>
            <input className="zz-input" value={f.titulo} maxLength={MAX_TITULO} data-testid="guia-titulo"
              placeholder="Melhor época para visitar os Lençóis Maranhenses"
              onChange={(e) => mexer({ titulo: e.target.value })} />
          </Campo>

          <Campo label="Resumo" contador={<Contador n={f.resumo.length} max={MAX_RESUMO} />}
            hint="Aparece no Google embaixo do título, e no cartão do guia na lista do site.">
            <textarea className="zz-input min-h-[60px]" value={f.resumo} maxLength={MAX_RESUMO} data-testid="guia-resumo"
              placeholder="Quando as lagoas estão cheias, o clima mês a mês e quando evitar."
              onChange={(e) => mexer({ resumo: e.target.value })} />
          </Campo>

          <Campo label="Texto" contador={<Contador n={f.corpo.length} max={MAX_CORPO} />}>
            <div className="text-[11px] text-ink-3 mb-2 space-y-0.5 border border-line rounded-lg bg-surface-2 px-3 py-2"
              data-testid="guia-ajuda-formato">
              <div>Texto simples, sem HTML.</div>
              <div>Linha que começa com <code className="text-ink-2">## </code> vira subtítulo; com <code className="text-ink-2">- </code>, item de lista.</div>
              <div>Uma linha em branco separa os parágrafos.</div>
            </div>
            <textarea className="zz-input min-h-[420px] leading-relaxed" value={f.corpo} maxLength={MAX_CORPO}
              data-testid="guia-corpo" spellCheck
              placeholder={"Conte a resposta curta em duas ou três frases.\n\n## Primeiro assunto\nO que a equipe sabe sobre isso.\n\n## Dicas\n- Primeira dica\n- Segunda dica"}
              onChange={(e) => mexer({ corpo: e.target.value })} />
            <div className="text-[11px] text-ink-3 mt-1 tabular-nums">
              {contaPalavras(f.corpo)} palavras · cerca de {minutosDeLeitura(f.corpo)} min de leitura
            </div>
          </Campo>

          <Campo label="Capa" hint="Opcional. Aparece no topo do guia e no cartão da lista. Até 5 MB.">
            <ImagemUnica id={f.capa_id} onChange={(id) => mexer({ capa_id: id })} rotulo="Capa do guia"
              testid="guia-capa" proporcao="aspect-[16/9]" />
          </Campo>

          <Campo label={`Passeios relacionados (${(f.passeios || []).length}/${MAX_PASSEIOS})`}
            hint="Aparecem no fim do guia, e o guia aparece na página deles.">
            {ativos.length === 0 && orfaos.length === 0 ? (
              <p className="text-xs text-ink-3">Nenhum passeio ativo cadastrado.</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2" data-testid="guia-passeios">
                {ativos.map((s) => {
                  const marcado = (f.passeios || []).includes(s.id);
                  return (
                    <label key={s.id}
                      className={`flex items-center gap-2 text-sm border rounded-lg px-3 py-2 cursor-pointer ${
                        marcado ? "border-info ring-1 ring-info bg-surface" : "border-line bg-surface-2"}`}>
                      <input type="checkbox" checked={marcado} data-testid={`guia-passeio-${s.id}`}
                        onChange={() => alternaPasseio(s.id)} />
                      <span className="truncate">{s.nome}</span>
                      {s.ativo === false && <span className="text-[11px] text-ink-3 shrink-0">(inativo)</span>}
                    </label>
                  );
                })}
                {orfaos.map((id) => (
                  <label key={id} className="flex items-center gap-2 text-sm border border-info ring-1 ring-info rounded-lg px-3 py-2 cursor-pointer">
                    <input type="checkbox" checked data-testid={`guia-passeio-${id}`} onChange={() => alternaPasseio(id)} />
                    <span className="truncate text-ink-3">Passeio removido</span>
                  </label>
                ))}
              </div>
            )}
          </Campo>

          <Campo label="Endereço da página"
            hint={f.id
              ? "Não muda quando o título muda, para os links já divulgados continuarem funcionando. Trocar aqui faz o endereço antigo parar."
              : "Vazio, é gerado a partir do título ao salvar. Depois não muda sozinho quando o título muda."}>
            <CampoComPrefixo prefixo="/guia/" valor={f.slug} testid="guia-slug"
              placeholder={f.id ? "" : "gerado a partir do título"}
              onChange={(v) => mexer({ slug: paraSlugGuia(v) })} />
          </Campo>

          <div className="flex items-start justify-between gap-4 border-t border-line pt-4">
            <div>
              <div className="text-sm font-medium text-ink-2">Publicado</div>
              <div className="text-[11px] text-ink-3 mt-0.5">
                Desligado, o guia fica em rascunho: só abre pela pré-visualização.
              </div>
            </div>
            <Interruptor ligado={f.publicada} onChange={(v) => mexer({ publicada: v })}
              testid="guia-publicada" rotulo="Publicado" />
          </div>

          {f.publicada && (sobra || curto) && (
            <div className="flex gap-2 border border-warning bg-warning-soft text-warning rounded-lg p-3 text-xs"
              data-testid="guia-aviso-publicar">
              <Warning size={16} weight="regular" className="shrink-0 mt-0.5" />
              <span>
                {sobra
                  ? `Ainda há texto de orientação do modelo ("${sobra.slice(0, 70)}${sobra.length > 70 ? "…" : ""}"). Troque pelo conteúdo da agência antes de publicar.`
                  : `O texto tem menos de ${MIN_PUBLICAR} caracteres: curto demais para publicar.`}
              </span>
            </div>
          )}

          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <button type="button" className="zz-btn zz-btn-secondary" data-testid="guia-cancelar"
              disabled={salvando} onClick={cancelar}>
              Cancelar
            </button>
            <button type="button" className="zz-btn" data-testid="guia-salvar"
              disabled={salvando} onClick={() => salvar()}>
              {salvando ? "Salvando…" : "Salvar"}
            </button>
          </div>
        </div>

        <div className="lg:sticky lg:top-4">
          <div className="text-xs text-ink-3 mb-2">Como o texto vai aparecer no site</div>
          <Previa titulo={f.titulo} resumo={f.resumo} corpo={f.corpo} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Página
// ---------------------------------------------------------------------------

export default function GuiasPage() {
  const [dados, setDados] = useState(null);
  const [site, setSite] = useState(null);
  const [servicos, setServicos] = useState([]);
  const [erro, setErro] = useState(null);
  const [editor, setEditor] = useState(null); // { inicial, original }
  const [escolhendo, setEscolhendo] = useState(false);

  const carregar = useCallback(async () => {
    try {
      const r = await api.get("/site/paginas");
      setDados(r.data);
      setErro(null);
    } catch (e) {
      setErro(formatApiErrorDetail(e.response?.data?.detail) || "Não foi possível carregar os guias");
    }
  }, []);

  useEffect(() => {
    carregar();
    api.get("/site").then((r) => setSite(r.data)).catch(() => {});
    api.get("/agenda/services").then((r) => setServicos(r.data || [])).catch(() => {});
  }, [carregar]);

  const instrucoes = useMemo(() => instrucoesDos(dados?.modelos), [dados]);

  const [excluir] = useAcao(async (g) => {
    if (!(await confirmToast(`Excluir o guia "${g.titulo}"? O endereço /guia/${g.slug} deixa de abrir.`,
      { confirmLabel: "Excluir" }))) return;
    try {
      await api.delete(`/site/paginas/${g.id}`);
      toast.success("Guia excluído");
      await carregar();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail) || "Não foi possível excluir o guia");
    }
  });

  const abrir = (g) => {
    const form = g ? paraForm(g) : vazio();
    setEditor({ inicial: form, original: form });
    setEscolhendo(false);
    window.scrollTo(0, 0);
  };
  const doModelo = (m) => {
    const form = { ...vazio(), titulo: m.titulo || "", resumo: m.resumo || "", corpo: m.corpo || "" };
    // O original é o guia vazio: começar de um modelo e cancelar pede confirmação.
    setEditor({ inicial: form, original: vazio() });
    setEscolhendo(false);
    window.scrollTo(0, 0);
  };

  if (editor) {
    return (
      <Editor inicial={editor.inicial} original={editor.original} servicos={servicos}
        instrucoes={instrucoes} site={site}
        onCancelar={() => setEditor(null)}
        onSalvo={async () => { setEditor(null); await carregar(); }} />
    );
  }

  const cabecalho = (
    <div className="mb-6">
      <Link to="/app/site" className="text-sm text-brand inline-flex items-center gap-1 hover:underline mb-3"
        data-testid="guias-voltar-site">
        <ArrowLeft size={14} weight="regular" /> Site
      </Link>
      <h1 className="font-heading text-4xl font-semibold">Guias do site</h1>
      <p className="text-sm text-ink-2 mt-2 max-w-2xl">
        Páginas de conteúdo sobre a região, como "Melhor época para visitar os Lençóis" ou "Como
        chegar a Barreirinhas". É o tipo de página que o Google e as IAs citam quando alguém
        pergunta sobre o destino, e cada uma leva aos passeios da agência.
      </p>
    </div>
  );

  if (erro && !dados) {
    return (
      <div className="zz-page-form" data-testid="guias-page">
        {cabecalho}
        <div className="border border-danger bg-danger-soft text-danger rounded-xl p-4 text-sm">{erro}</div>
      </div>
    );
  }
  if (!dados) {
    return <div className="zz-page-form text-sm text-ink-3" data-testid="guias-page">Carregando…</div>;
  }

  const guias = dados.guias || [];
  const modelos = dados.modelos || [];
  const max = dados.max || 30;
  const cheio = guias.length >= max;

  return (
    <div className="zz-page-form" data-testid="guias-page">
      {cabecalho}

      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="text-sm text-ink-2 tabular-nums" data-testid="guias-contagem">
          {guias.length} de {max} guias
        </div>
        <div className="flex flex-col items-end">
          <button type="button" className="zz-btn" data-testid="guias-novo" disabled={cheio}
            onClick={() => setEscolhendo((x) => !x)}>
            <Plus size={14} weight="regular" /> Novo guia
          </button>
          {cheio && (
            <span className="text-[11px] text-ink-3 mt-1" data-testid="guias-limite">
              O site tem o máximo de {max} guias. Exclua um para criar outro.
            </span>
          )}
        </div>
      </div>

      {guias.length === 0 ? (
        <section className="border border-line bg-surface rounded-xl p-6" data-testid="guias-vazio">
          <div className="flex items-center gap-2 mb-1">
            <BookOpenText size={16} weight="regular" className="text-ink-3" />
            <h2 className="font-heading text-lg">Nenhum guia ainda.</h2>
          </div>
          <p className="text-xs text-ink-3 mb-4">Comece por um dos modelos abaixo.</p>
          <Modelos modelos={modelos} onEscolher={doModelo} onEmBranco={() => abrir(null)} desabilitado={cheio} />
        </section>
      ) : (
        <>
          {escolhendo && !cheio && (
            <section className="border border-line bg-surface rounded-xl p-6 mb-4" data-testid="guias-escolher">
              <h2 className="font-heading text-lg mb-1">Como começar</h2>
              <Modelos modelos={modelos} onEscolher={doModelo} onEmBranco={() => abrir(null)} desabilitado={cheio} />
            </section>
          )}
          <ul className="space-y-3" data-testid="guias-lista">
            {guias.map((g) => {
              const ver = g.publicada ? g.url : urlPreviaGuia(site, g.slug);
              return (
                <li key={g.id} className="border border-line bg-surface rounded-xl p-4" data-testid={`guia-${g.id}`}>
                  <div className="flex flex-col sm:flex-row sm:items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-ink break-words">{g.titulo}</span>
                        <Status publicada={g.publicada} testid={`guia-status-${g.id}`} />
                      </div>
                      {g.resumo && <p className="text-xs text-ink-2 mt-1">{g.resumo}</p>}
                      <div className="text-[11px] text-ink-3 mt-1 tabular-nums">
                        /guia/{g.slug} · {minutosDeLeitura(g.corpo)} min de leitura
                        {(g.passeios || []).length > 0 && ` · ${g.passeios.length} passeio(s) relacionado(s)`}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 shrink-0">
                      <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid={`guia-editar-${g.id}`}
                        onClick={() => abrir(g)}>
                        <PencilSimple size={13} weight="regular" /> Editar
                      </button>
                      {ver ? (
                        <a href={ver} target="_blank" rel="noopener noreferrer" data-testid={`guia-ver-${g.id}`}
                          className="zz-btn zz-btn-secondary text-xs">
                          {g.publicada ? <ArrowSquareOut size={13} weight="regular" /> : <Eye size={13} weight="regular" />}
                          {g.publicada ? "Ver no site" : "Pré-visualizar"}
                        </a>
                      ) : (
                        <span className="text-[11px] text-ink-3" title="O site ainda não tem endereço">Sem endereço</span>
                      )}
                      <button type="button" className="zz-btn-icon" title="Excluir" aria-label="Excluir guia"
                        data-testid={`guia-excluir-${g.id}`} onClick={() => excluir(g)}>
                        <Trash size={14} weight="regular" />
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
