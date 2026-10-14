import { useRef, useState } from "react";
import { toast } from "sonner";
import {
  ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Plus, Trash, UploadSimple, X,
} from "@phosphor-icons/react";
import api, { API, formatApiErrorDetail } from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { CampoHora, REGEX_RELOGIO } from "@/components/agenda/CampoHora";

// Peças do site que a tela "Site" e o formulário do passeio usam juntos:
// upload de imagem, lista de itens curtos, galeria de fotos e a seção
// "Página no site" do passeio. Ficam num lugar só para as duas telas não
// divergirem no que mandam ao backend.

/** Teto do `POST /api/site/midia` (o backend recusa acima disso). */
export const MAX_IMAGEM_MB = 5;
export const MAX_FOTOS = 10;
export const MAX_ITENS = 20;
export const MAX_ITEM = 80;
export const MAX_RESUMO = 160;
export const MAX_DESCRICAO = 4000;
export const MAX_PERGUNTA = 150;
export const MAX_RESPOSTA = 800;
export const MAX_PERGUNTAS_PASSEIO = 10;
export const MAX_ROTEIRO = 15;
export const MAX_PASSO = 160;
export const MAX_LEGENDA = 120;
export const MAX_MELHOR_EPOCA = 160;
export const MAX_CHUVA = 400;

export const NIVEIS = [
  ["leve", "Leve"],
  ["moderado", "Moderado"],
  ["intenso", "Intenso"],
];

/** Pré-visualização autenticada pelo cookie do painel.
 *
 * Montada pelo id, e não pela `url` devolvida no upload: aquela é relativa ao
 * backend (`/api/media/…`), e o painel pode estar noutro endereço que a API. */
export function urlMidia(id) {
  return id ? `${API}/media/${id}` : "";
}

/** "Lagoa Azul & Circuito" → "lagoa-azul-circuito". Não apara o hífen do fim,
 * para quem está digitando conseguir chegar à palavra seguinte. */
export function paraSlug(texto) {
  return String(texto || "")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 40);
}

export function slugFinal(texto) {
  return paraSlug(texto).replace(/-+$/, "");
}

/** Sobe uma imagem e devolve o id. Recusa com motivo o que não serve,
 * em vez de deixar o seletor do sistema esconder o arquivo. */
export async function enviarImagem(arquivo) {
  if (!arquivo) return null;
  if (!(arquivo.type || "").startsWith("image/")) {
    toast.error(`"${arquivo.name}" não é uma imagem. Envie JPG, PNG ou WebP.`);
    return null;
  }
  if (arquivo.size > MAX_IMAGEM_MB * 1024 * 1024) {
    const mb = (arquivo.size / 1024 / 1024).toFixed(1).replace(".", ",");
    toast.error(`"${arquivo.name}" tem ${mb} MB. O limite é ${MAX_IMAGEM_MB} MB por imagem.`);
    return null;
  }
  const fd = new FormData();
  fd.append("file", arquivo);
  try {
    const r = await api.post("/site/midia", fd);
    return r.data?.id || null;
  } catch (e) {
    toast.error(formatApiErrorDetail(e.response?.data?.detail) || "Não foi possível enviar a imagem");
    return null;
  }
}

/** Botão de enviar arquivo. Sem `accept`: arquivo fora da lista aparece apagado
 * no seletor do sistema, sem mensagem nenhuma. A recusa é feita aqui, com motivo. */
export function BotaoEnviar({ onArquivos, multiplo = false, ocupado, rotulo, testid, disabled }) {
  const ref = useRef(null);
  return (
    <>
      <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid={testid}
        disabled={ocupado || disabled} onClick={() => ref.current?.click()}>
        <UploadSimple size={14} weight="regular" /> {ocupado ? "Enviando…" : rotulo}
      </button>
      <input ref={ref} type="file" className="hidden" multiple={multiplo}
        data-testid={testid ? `${testid}-input` : undefined}
        onChange={(e) => {
          const lista = Array.from(e.target.files || []);
          e.target.value = "";
          if (lista.length) onArquivos(lista);
        }} />
    </>
  );
}

/** Campo de texto com pedaços fixos antes e depois ("https://" [slug] ".dominio").
 *
 * A borda é do invólucro, não do `input`: `.zz-input` vem depois das
 * utilidades no CSS, e um `rounded-r-none` nele não pegaria. */
export function CampoComPrefixo({ prefixo, sufixo, valor, onChange, placeholder, testid, onBlur }) {
  return (
    <div className="flex items-stretch border border-line-strong rounded-[10px] bg-surface overflow-hidden focus-within:border-info focus-within:ring-2 focus-within:ring-info-soft">
      {prefixo && (
        <span className="flex items-center px-3 bg-surface-2 text-ink-3 text-sm tabular-nums border-r border-line shrink-0">
          {prefixo}
        </span>
      )}
      <input className="flex-1 min-w-0 bg-transparent outline-none px-3 py-[0.6rem] text-sm text-ink placeholder:text-ink-4"
        value={valor} placeholder={placeholder} data-testid={testid} onBlur={onBlur}
        spellCheck={false} autoCapitalize="off" autoComplete="off"
        onChange={(e) => onChange(e.target.value)} />
      {sufixo && (
        <span className="flex items-center px-3 bg-surface-2 text-ink-3 text-sm tabular-nums border-l border-line shrink-0 max-w-[50%] truncate">
          {sufixo}
        </span>
      )}
    </div>
  );
}

/** Uma imagem só (logo, capa): pré-visualização, trocar e remover. */
export function ImagemUnica({ id, onChange, rotulo, testid, proporcao = "aspect-[3/1]", ajuda }) {
  const [enviar, enviando] = useAcao(async ([arquivo]) => {
    const novo = await enviarImagem(arquivo);
    if (novo) onChange(novo);
  });
  return (
    <div data-testid={testid}>
      <div className={`border border-line rounded-lg bg-surface-2 overflow-hidden flex items-center justify-center ${proporcao}`}>
        {id ? (
          <img src={urlMidia(id)} alt={rotulo} className="w-full h-full object-cover"
            data-testid={`${testid}-preview`} />
        ) : (
          <span className="text-xs text-ink-3 p-3 text-center">Sem imagem</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 mt-2">
        <BotaoEnviar onArquivos={enviar} ocupado={enviando} testid={`${testid}-enviar`}
          rotulo={id ? "Trocar" : "Enviar imagem"} />
        {id && (
          <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid={`${testid}-remover`}
            onClick={() => onChange(null)}>
            <X size={14} weight="regular" /> Remover
          </button>
        )}
      </div>
      {ajuda && <p className="text-[11px] text-ink-3 mt-1">{ajuda}</p>}
    </div>
  );
}

/** Lista editável de itens curtos ("Transporte 4x4", "Protetor solar"). */
export function ListaItens({ itens, onChange, placeholder, testid, max = MAX_ITENS, tamanho = MAX_ITEM }) {
  const [novo, setNovo] = useState("");
  const lista = itens || [];
  const cheia = lista.length >= max;
  const adicionar = () => {
    const t = novo.trim();
    if (!t) return;
    if (lista.some((x) => x.toLowerCase() === t.toLowerCase())) {
      toast.error(`"${t}" já está na lista`);
      return;
    }
    onChange([...lista, t]);
    setNovo("");
  };
  return (
    <div className="space-y-2" data-testid={testid}>
      {lista.map((item, i) => (
        <div key={i} className="flex items-center gap-2">
          <input className="zz-input flex-1" value={item} maxLength={tamanho}
            data-testid={`${testid}-item-${i}`}
            onChange={(e) => onChange(lista.map((x, k) => (k === i ? e.target.value : x)))} />
          <button type="button" className="zz-btn-icon" title="Remover" aria-label="Remover"
            data-testid={`${testid}-remover-${i}`}
            onClick={() => onChange(lista.filter((_, k) => k !== i))}>
            <Trash size={14} weight="regular" />
          </button>
        </div>
      ))}
      {cheia ? (
        <p className="text-[11px] text-ink-3">Até {max} itens.</p>
      ) : (
        <div className="flex items-center gap-2">
          <input className="zz-input flex-1" value={novo} maxLength={tamanho} placeholder={placeholder}
            data-testid={`${testid}-novo`}
            onChange={(e) => setNovo(e.target.value)}
            // Enter dentro do Modal enviaria o formulário inteiro: aqui ele
            // acrescenta o item, que é o que quem está digitando uma lista espera.
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); adicionar(); } }} />
          <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid={`${testid}-add`}
            disabled={!novo.trim()} onClick={adicionar}>
            <Plus size={12} weight="regular" /> Adicionar
          </button>
        </div>
      )}
    </div>
  );
}

/** Galeria do passeio: até 10, reordenável, a primeira é a capa. */
export function FotosSite({ fotos, onChange, testid = "site-fotos", legendas, onLegendas }) {
  const lista = fotos || [];
  const [enviar, enviando] = useAcao(async (arquivos) => {
    const vagas = MAX_FOTOS - lista.length;
    if (arquivos.length > vagas) {
      toast.error(vagas > 0
        ? `Cabem mais ${vagas} foto(s): o passeio tem até ${MAX_FOTOS}.`
        : `O passeio já tem ${MAX_FOTOS} fotos. Remova uma para enviar outra.`);
    }
    const novas = [];
    for (const arq of arquivos.slice(0, Math.max(0, vagas))) {
      // Uma de cada vez: são poucas, e em sequência a ordem do seletor é a
      // ordem da galeria.
      // eslint-disable-next-line no-await-in-loop
      const id = await enviarImagem(arq);
      if (id) novas.push(id);
    }
    if (novas.length) onChange([...lista, ...novas]);
  });
  const mover = (i, d) => {
    const j = i + d;
    if (j < 0 || j >= lista.length) return;
    const nova = [...lista];
    [nova[i], nova[j]] = [nova[j], nova[i]];
    onChange(nova);
  };
  return (
    <div data-testid={testid}>
      {lista.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 mb-2">
          {lista.map((id, i) => (
            <div key={id} className={`border rounded-lg overflow-hidden bg-surface-2 ${
              i === 0 ? "border-info ring-1 ring-info" : "border-line"}`} data-testid={`${testid}-${i}`}>
              <div className="aspect-[4/3] relative">
                <img src={urlMidia(id)} alt={`Foto ${i + 1}`} className="w-full h-full object-cover" />
                {i === 0 && (
                  <span className="absolute top-1 left-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-brand text-brand-fg">
                    Capa
                  </span>
                )}
              </div>
              <div className="flex items-center justify-between px-1 py-1 bg-surface">
                <button type="button" className="zz-btn-icon" title="Mover para a esquerda"
                  aria-label="Mover para a esquerda" disabled={i === 0} onClick={() => mover(i, -1)}>
                  <ArrowLeft size={13} weight="regular" />
                </button>
                <button type="button" className="zz-btn-icon" title="Remover foto" aria-label="Remover foto"
                  data-testid={`${testid}-${i}-remover`}
                  onClick={() => onChange(lista.filter((_, k) => k !== i))}>
                  <Trash size={13} weight="regular" />
                </button>
                <button type="button" className="zz-btn-icon" title="Mover para a direita"
                  aria-label="Mover para a direita" disabled={i === lista.length - 1} onClick={() => mover(i, 1)}>
                  <ArrowRight size={13} weight="regular" />
                </button>
              </div>
              {onLegendas && (
                <div className="px-1 pb-1 bg-surface">
                  <input className="zz-input text-xs" value={(legendas || {})[id] || ""}
                    maxLength={MAX_LEGENDA} placeholder="Legenda (opcional)"
                    aria-label={`Legenda da foto ${i + 1}`}
                    data-testid={`passeio-foto-legenda-${i}`}
                    onChange={(e) => onLegendas({ ...(legendas || {}), [id]: e.target.value })} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <BotaoEnviar onArquivos={enviar} ocupado={enviando} multiplo testid={`${testid}-enviar`}
          disabled={lista.length >= MAX_FOTOS} rotulo="Enviar fotos" />
        <span className="text-[11px] text-ink-3">
          {lista.length} de {MAX_FOTOS} · a primeira é a capa · até {MAX_IMAGEM_MB} MB cada
        </span>
      </div>
    </div>
  );
}

export function Contador({ n, max }) {
  return (
    <span className={`text-[11px] tabular-nums ${n > max ? "text-danger" : "text-ink-3"}`}>
      {n}/{max}
    </span>
  );
}

/** Lista editável de objetos (depoimentos, perguntas, passos do roteiro):
 * adicionar, remover e mover para cima/baixo. O desenho de cada item é de
 * quem usa (`renderItem(item, i, mexer)`); o resto mora aqui, para as quatro
 * listas se comportarem igual. */
// `compacto`: os botões ficam em linha, ao lado de um item de uma linha só (o
// passo do roteiro); empilhados, eles triplicavam a altura de cada passo.
export function ListaObjetos({ itens, onChange, novo, max, testid, rotuloAdicionar = "Adicionar", renderItem, compacto = false }) {
  const lista = itens || [];
  const mexer = (i) => (patch) => onChange(lista.map((x, k) => (k === i ? { ...x, ...patch } : x)));
  const mover = (i, d) => {
    const j = i + d;
    if (j < 0 || j >= lista.length) return;
    const nova = [...lista];
    [nova[i], nova[j]] = [nova[j], nova[i]];
    onChange(nova);
  };
  return (
    <div className="space-y-2" data-testid={testid}>
      {lista.map((item, i) => (
        // Índice como chave: os itens não têm id, e a ordem é a da lista.
        <div key={i} className={`border border-line rounded-lg bg-surface-2 flex gap-2 ${compacto ? "p-2 items-center" : "p-3"}`}
          data-testid={`${testid}-${i}`}>
          <div className="flex-1 min-w-0 space-y-2">{renderItem(item, i, mexer(i))}</div>
          <div className={`flex gap-1 shrink-0 ${compacto ? "flex-row" : "flex-col"}`}>
            <button type="button" className="zz-btn-icon" title="Mover para cima" aria-label="Mover para cima"
              data-testid={`${testid}-subir-${i}`} disabled={i === 0} onClick={() => mover(i, -1)}>
              <ArrowUp size={13} weight="regular" />
            </button>
            <button type="button" className="zz-btn-icon" title="Mover para baixo" aria-label="Mover para baixo"
              data-testid={`${testid}-descer-${i}`} disabled={i === lista.length - 1} onClick={() => mover(i, 1)}>
              <ArrowDown size={13} weight="regular" />
            </button>
            <button type="button" className="zz-btn-icon" title="Remover" aria-label="Remover"
              data-testid={`${testid}-remover-${i}`}
              onClick={() => onChange(lista.filter((_, k) => k !== i))}>
              <Trash size={13} weight="regular" />
            </button>
          </div>
        </div>
      ))}
      {lista.length >= max ? (
        <p className="text-[11px] text-ink-3">Até {max} itens.</p>
      ) : (
        <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid={`${testid}-add`}
          onClick={() => onChange([...lista, novo()])}>
          <Plus size={12} weight="regular" /> {rotuloAdicionar}
        </button>
      )}
    </div>
  );
}

/** Perguntas frequentes (da agência e do passeio). */
export function EditorPerguntas({ itens, onChange, max, testid }) {
  return (
    <ListaObjetos itens={itens} onChange={onChange} max={max} testid={testid}
      rotuloAdicionar="Adicionar pergunta" novo={() => ({ pergunta: "", resposta: "" })}
      renderItem={(p, i, mexer) => (
        <>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-ink-2">Pergunta</label>
              <Contador n={(p.pergunta || "").length} max={MAX_PERGUNTA} />
            </div>
            <input className="zz-input" value={p.pergunta || ""} maxLength={MAX_PERGUNTA}
              data-testid={`${testid}-pergunta-${i}`} placeholder="Criança paga?"
              onChange={(e) => mexer({ pergunta: e.target.value })} />
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-ink-2">Resposta</label>
              <Contador n={(p.resposta || "").length} max={MAX_RESPOSTA} />
            </div>
            <textarea className="zz-input min-h-[70px]" value={p.resposta || ""} maxLength={MAX_RESPOSTA}
              data-testid={`${testid}-resposta-${i}`}
              onChange={(e) => mexer({ resposta: e.target.value })} />
          </div>
        </>
      )} />
  );
}

/** Perguntas sem os itens totalmente vazios, aparadas. */
export function limpaPerguntas(lista, max) {
  return (lista || [])
    .map((p) => ({ pergunta: (p.pergunta || "").trim(), resposta: (p.resposta || "").trim() }))
    .filter((p) => p.pergunta || p.resposta)
    .slice(0, max);
}

/** Pergunta sem resposta (ou o contrário), dito antes do 400. */
export function problemaPerguntas(lista, rotulo) {
  const n = (lista || []).findIndex((p) => {
    const temP = !!(p.pergunta || "").trim();
    const temR = !!(p.resposta || "").trim();
    return temP !== temR;
  });
  return n >= 0 ? `${rotulo}: a pergunta ${n + 1} precisa da pergunta e da resposta` : null;
}

/** O "como é o dia" do passeio: hora opcional e o que acontece. */
function EditorRoteiro({ itens, onChange, testid }) {
  return (
    <ListaObjetos itens={itens} onChange={onChange} max={MAX_ROTEIRO} testid={testid} compacto
      rotuloAdicionar="Adicionar passo" novo={() => ({ hora: null, texto: "" })}
      renderItem={(p, i, mexer) => (
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          <div className="flex items-center gap-1 shrink-0">
            <CampoHora valor={p.hora || ""} testid={`${testid}-hora-${i}`}
              onChange={(h) => mexer({ hora: h })} />
            {p.hora && (
              <button type="button" className="zz-btn-icon" title="Tirar a hora" aria-label="Tirar a hora"
                data-testid={`${testid}-hora-limpar-${i}`} onClick={() => mexer({ hora: null })}>
                <X size={12} weight="regular" />
              </button>
            )}
          </div>
          <input className="zz-input flex-1 min-w-0" value={p.texto || ""} maxLength={MAX_PASSO}
            data-testid={`${testid}-texto-${i}`}
            placeholder={i === 0 ? "Busca na pousada" : "Travessia do rio"}
            onChange={(e) => mexer({ texto: e.target.value })} />
        </div>
      )} />
  );
}

function textoOuNulo(v) {
  return (String(v ?? "")).trim() || null;
}

/** Os campos de site de um passeio, prontos para o corpo do POST/PATCH. */
export function camposSite(s) {
  const limpa = (l) => (l || []).map((x) => String(x).trim()).filter(Boolean).slice(0, MAX_ITENS);
  const fotos = (s.site_fotos || []).slice(0, MAX_FOTOS);
  // Legenda só das fotos que ficaram: a de foto removida o backend descartaria.
  const legendas = {};
  fotos.forEach((id) => {
    const t = String((s.site_legendas || {})[id] || "").trim();
    if (t) legendas[id] = t;
  });
  const idade = s.site_idade_minima;
  return {
    site_roteiro: (s.site_roteiro || [])
      .map((p) => ({ hora: p.hora || null, texto: (p.texto || "").trim() }))
      .filter((p) => p.hora || p.texto)
      .slice(0, MAX_ROTEIRO),
    site_nivel: NIVEIS.some(([id]) => id === s.site_nivel) ? s.site_nivel : null,
    site_idade_minima: idade === "" || idade == null ? null : Number(idade),
    site_melhor_epoca: textoOuNulo(s.site_melhor_epoca),
    site_chuva: textoOuNulo(s.site_chuva),
    site_faq: limpaPerguntas(s.site_faq, MAX_PERGUNTAS_PASSEIO),
    site_legendas: legendas,
    site_visivel: s.site_visivel !== false,
    site_resumo: (s.site_resumo || "").trim() || null,
    site_descricao: (s.site_descricao || "").trim() || null,
    site_inclui: limpa(s.site_inclui),
    site_levar: limpa(s.site_levar),
    site_fotos: fotos,
    // Vazio na criação: o servidor gera a partir do nome.
    site_slug: slugFinal(s.site_slug) || null,
  };
}

/** O que impede de salvar os campos de site, dito antes do 400. */
export function problemaSite(s) {
  if ((s.site_resumo || "").length > MAX_RESUMO) return `Resumo do site: até ${MAX_RESUMO} caracteres`;
  if ((s.site_descricao || "").length > MAX_DESCRICAO) return `Descrição do site: até ${MAX_DESCRICAO} caracteres`;
  const slug = slugFinal(s.site_slug);
  if (s.site_slug && slug.length < 3) return "Endereço da página do passeio: pelo menos 3 letras";
  const passo = (s.site_roteiro || []).findIndex((p) => p.hora && !(p.texto || "").trim());
  if (passo >= 0) return `Roteiro: o passo ${passo + 1} tem hora mas não diz o que acontece`;
  if ((s.site_roteiro || []).some((p) => p.hora && !REGEX_RELOGIO.test(p.hora))) {
    return "Roteiro: hora inválida (use 07:30)";
  }
  if ((s.site_melhor_epoca || "").length > MAX_MELHOR_EPOCA) return `Melhor época: até ${MAX_MELHOR_EPOCA} caracteres`;
  if ((s.site_chuva || "").length > MAX_CHUVA) return `Se chover: até ${MAX_CHUVA} caracteres`;
  return problemaPerguntas(s.site_faq, "Perguntas do passeio");
}

/** O passeio inteiro, como o `ServicoIn` espera. O PATCH regrava o documento
 * com o que vier, então mandar só os campos de site apagaria a grade. */
export function corpoServico(s) {
  return {
    nome: s.nome,
    descricao: s.descricao ?? null,
    duracao_min: s.duracao_min,
    preco: s.preco ?? null,
    preco_crianca: s.preco_crianca ?? null,
    capacidade: s.capacidade,
    grade_saidas: s.grade_saidas ?? null,
    excecoes_saidas: s.excecoes_saidas || [],
    cor: s.cor ?? null,
    ativo: s.ativo !== false,
    ordem: s.ordem ?? 0,
    recursos: s.recursos || [],
    ...camposSite(s),
  };
}

/** Seção "Página no site" do passeio. `valor` é o passeio em edição. */
export function CamposSitePasseio({ valor, onChange }) {
  const mexer = (patch) => onChange({ ...valor, ...patch });
  return (
    <div className="space-y-4" data-testid="secao-site-passeio">
      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input type="checkbox" className="mt-0.5" checked={valor.site_visivel !== false}
          data-testid="site-visivel"
          onChange={(e) => mexer({ site_visivel: e.target.checked })} />
        <span>
          Aparece no site
          <span className="block text-[11px] text-ink-3">
            Desligado, o passeio continua à venda no WhatsApp e nas Saídas, só não aparece no site.
          </span>
        </span>
      </label>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-sm font-medium text-ink-2">Resumo</label>
          <Contador n={(valor.site_resumo || "").length} max={MAX_RESUMO} />
        </div>
        <input className="zz-input" value={valor.site_resumo || ""} maxLength={MAX_RESUMO}
          data-testid="site-resumo"
          placeholder="Uma frase: o que o turista vai ver e por que vale a pena."
          onChange={(e) => mexer({ site_resumo: e.target.value })} />
        <p className="text-[11px] text-ink-3 mt-1">
          Vai no cartão do passeio e na descrição que aparece no Google.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-sm font-medium text-ink-2">Descrição da página</label>
          <Contador n={(valor.site_descricao || "").length} max={MAX_DESCRICAO} />
        </div>
        <textarea className="zz-input min-h-[140px]" value={valor.site_descricao || ""}
          maxLength={MAX_DESCRICAO} data-testid="site-descricao"
          placeholder="Como é o passeio, o roteiro, o que esperar. Deixe uma linha em branco entre os parágrafos."
          onChange={(e) => mexer({ site_descricao: e.target.value })} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">O que está incluído</label>
          <ListaItens itens={valor.site_inclui} testid="site-inclui" placeholder="Transporte 4x4"
            onChange={(l) => mexer({ site_inclui: l })} />
        </div>
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">O que levar</label>
          <ListaItens itens={valor.site_levar} testid="site-levar" placeholder="Protetor solar"
            onChange={(l) => mexer({ site_levar: l })} />
        </div>
      </div>

      <div>
        <label className="text-sm font-medium text-ink-2 block mb-1.5">Como é o dia</label>
        <p className="text-[11px] text-ink-3 mb-2">
          O roteiro, passo a passo. A hora é opcional. Só aparece no site quando tiver pelo menos um passo.
        </p>
        <EditorRoteiro itens={valor.site_roteiro} testid="passeio-roteiro"
          onChange={(l) => mexer({ site_roteiro: l })} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">Esforço</label>
          <select className="zz-input" value={valor.site_nivel || ""} data-testid="passeio-nivel"
            onChange={(e) => mexer({ site_nivel: e.target.value || null })}>
            <option value="">Não informar</option>
            {NIVEIS.map(([id, rotulo]) => <option key={id} value={id}>{rotulo}</option>)}
          </select>
          <p className="text-[11px] text-ink-3 mt-1">
            {valor.site_nivel
              ? `Aparece como "Esforço ${(NIVEIS.find(([id]) => id === valor.site_nivel)?.[1] || "").toLowerCase()}" na página.`
              : "Leve, moderado ou intenso: ajuda o turista a escolher."}
          </p>
        </div>
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">Idade mínima</label>
          <select className="zz-input" data-testid="passeio-idade-minima"
            value={valor.site_idade_minima == null || valor.site_idade_minima === "" ? "" : String(valor.site_idade_minima)}
            onChange={(e) => mexer({ site_idade_minima: e.target.value === "" ? null : Number(e.target.value) })}>
            <option value="">Não informar</option>
            <option value="0">Todas as idades</option>
            {Array.from({ length: 18 }, (_, k) => k + 1).map((n) => (
              <option key={n} value={n}>A partir de {n} {n === 1 ? "ano" : "anos"}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-sm font-medium text-ink-2">Melhor época</label>
          <Contador n={(valor.site_melhor_epoca || "").length} max={MAX_MELHOR_EPOCA} />
        </div>
        <input className="zz-input" value={valor.site_melhor_epoca || ""} maxLength={MAX_MELHOR_EPOCA}
          data-testid="passeio-melhor-epoca"
          placeholder="De junho a setembro, quando as lagoas estão cheias"
          onChange={(e) => mexer({ site_melhor_epoca: e.target.value })} />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-sm font-medium text-ink-2">O que acontece se chover</label>
          <Contador n={(valor.site_chuva || "").length} max={MAX_CHUVA} />
        </div>
        <textarea className="zz-input min-h-[70px]" value={valor.site_chuva || ""} maxLength={MAX_CHUVA}
          data-testid="passeio-chuva"
          placeholder="Com chuva forte a saída é remarcada sem custo, ou o valor é devolvido."
          onChange={(e) => mexer({ site_chuva: e.target.value })} />
      </div>

      <div>
        <label className="text-sm font-medium text-ink-2 block mb-1.5">Fotos</label>
        <FotosSite fotos={valor.site_fotos} onChange={(l) => mexer({ site_fotos: l })}
          legendas={valor.site_legendas} onLegendas={(l) => mexer({ site_legendas: l })} />
        <p className="text-[11px] text-ink-3 mt-1">A legenda aparece embaixo da foto na galeria do site.</p>
      </div>

      <div>
        <label className="text-sm font-medium text-ink-2 block mb-1.5">Perguntas sobre este passeio</label>
        <p className="text-[11px] text-ink-3 mb-2">
          Só aparecem no site quando preenchidas. As perguntas gerais da agência ficam na tela Site.
        </p>
        <EditorPerguntas itens={valor.site_faq} max={MAX_PERGUNTAS_PASSEIO} testid="passeio-faq"
          onChange={(l) => mexer({ site_faq: l })} />
      </div>

      <div>
        <label className="text-sm font-medium text-ink-2 block mb-1.5">Endereço da página</label>
        <CampoComPrefixo prefixo="/passeio/" valor={valor.site_slug || ""} testid="site-slug-passeio"
          placeholder={valor.id ? "" : "gerado a partir do nome ao salvar"}
          onChange={(v) => mexer({ site_slug: paraSlug(v) })} />
        <p className="text-[11px] text-ink-3 mt-1">
          Não muda quando o nome do passeio muda, para os links já divulgados continuarem
          funcionando. Trocar aqui faz o endereço antigo parar.
        </p>
      </div>
    </div>
  );
}
