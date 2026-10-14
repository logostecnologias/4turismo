import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "@phosphor-icons/react";
import { useAcao } from "@/hooks/useAcao";

// Caixa de diálogo única do sistema.
//
// Nasceu dentro de `agenda/Catalogo.jsx` e saiu de lá quando as outras doze
// telas de cadastro deixaram de abrir uma faixa inline: cada uma tinha o seu
// próprio painel empurrando a lista para baixo, e os formulários grandes
// empurravam a tela inteira para fora da vista.
//
// Três coisas moram aqui de propósito, e não em quem abre o modal:
//
// - **A trava de clique repetido** (`useAcao`). Foi confiar na lembrança de
//   quem escreve a tela que produziu dois profissionais com um duplo clique em
//   "Salvar". Quem usa este Modal ganha a trava sem pedir.
// - **O corpo é um `<form>` de verdade** quando há `onSalvar`. É o que preserva
//   o `required` dos campos (a validação nativa do navegador) e o Enter que
//   envia — os formulários inline já contavam com os dois, e trocá-los por um
//   botão com `onClick` perderia as duas coisas em silêncio.
// - **Um portal para o `body`.** Sem ele, um modal aberto de dentro da gaveta
//   da ficha de contato (que é `position: fixed` com `z-40`, e portanto cria
//   contexto de empilhamento) ficaria preso atrás da própria gaveta.
//
// Fecha por Esc e por clique no fundo. O clique é conferido no `mousedown` e
// só quando ele nasce no próprio fundo: um arrasto que começa dentro do campo
// de texto e termina fora fecharia o modal e jogaria fora o que foi digitado.
//
// A pilha existe porque há modal em cima de modal (o gestor de campos
// personalizados abre o formulário de um campo). Com um listener de Esc por
// modal e nenhuma pilha, uma tecla fecharia os dois de uma vez: os dois
// listeners estão no mesmo `document`, e `stopPropagation` não alcança um
// irmão. Só quem está no topo responde.

const pilha = [];

export default function Modal({
  titulo,
  descricao,
  children,
  onFechar,
  onSalvar,
  largo,
  salvarLabel = "Salvar",
  salvandoLabel = "Salvando…",
  cancelarLabel = "Cancelar",
  fecharLabel = "Fechar",
  rodape,
  testId,
  // A grade do formulário é do próprio corpo do modal, e não de uma `div`
  // extra dentro dele: um invólucro só para segurar `grid-cols` acrescenta um
  // nível de indentação em toda tela que usa o Modal, sem desenhar nada.
  corpoClasse = "space-y-4",
}) {
  const [salvar, salvando] = useAcao(async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (onSalvar) await onSalvar(e);
  });

  // Esc fecha. O listener é do documento porque o foco pode estar em qualquer
  // campo de dentro, e o modal não é o elemento focado.
  const meuId = useRef({});
  useEffect(() => {
    const id = meuId.current;
    pilha.push(id);
    return () => {
      const i = pilha.indexOf(id);
      if (i >= 0) pilha.splice(i, 1);
    };
  }, []);

  useEffect(() => {
    if (!onFechar) return undefined;
    const aoTeclar = (e) => {
      if (e.key !== "Escape") return;
      if (pilha[pilha.length - 1] !== meuId.current) return;
      onFechar();
    };
    document.addEventListener("keydown", aoTeclar);
    return () => document.removeEventListener("keydown", aoTeclar);
  }, [onFechar]);

  // Trava o scroll da página atrás. Sem isso a roda do mouse sobre o fundo
  // rola a lista, e ao fechar o modal a tela está noutro lugar.
  //
  // Quem decide é a pilha, não cada modal guardando o valor anterior: os
  // efeitos do filho rodam antes dos do pai, então o modal de dentro guardaria
  // o `overflow` de antes de o de fora travar, e fechá-lo devolveria o scroll
  // à página com o de fora ainda aberto.
  useEffect(() => {
    if (pilha.length === 1) document.body.style.overflow = "hidden";
    return () => { if (pilha.length === 0) document.body.style.overflow = ""; };
  });

  const Corpo = onSalvar ? "form" : "div";
  const props = onSalvar ? { onSubmit: salvar, noValidate: false } : {};

  return createPortal(
    <div
      className="fixed inset-0 bg-black/50 z-[100] flex items-center justify-center p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget && onFechar) onFechar(); }}
      data-testid={testId}
    >
      <Corpo
        {...props}
        className={`bg-surface border border-line rounded-2xl w-full ${
          largo ? "max-w-4xl" : "max-w-2xl"
        } max-h-[90vh] flex flex-col shadow-pop`}
      >
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-line">
          <div>
            <h2 className="font-heading text-xl">{titulo}</h2>
            {descricao && <p className="text-xs text-ink-2 mt-1">{descricao}</p>}
          </div>
          <button type="button" onClick={onFechar} className="zz-btn-icon shrink-0"
            aria-label="Fechar">
            <X size={18} weight="regular" />
          </button>
        </div>

        <div className={`flex-1 overflow-y-auto p-6 ${corpoClasse}`}>{children}</div>

        <div className="border-t border-line px-6 py-4 flex justify-end gap-2">
          {rodape}
          {onSalvar ? (
            <>
              <button type="button" className="zz-btn zz-btn-secondary" onClick={onFechar}>
                {cancelarLabel}
              </button>
              <button type="submit" className="zz-btn" disabled={salvando}
                data-testid="modal-salvar">
                {salvando ? salvandoLabel : salvarLabel}
              </button>
            </>
          ) : (
            <button type="button" className="zz-btn zz-btn-secondary" onClick={onFechar}>
              {fecharLabel}
            </button>
          )}
        </div>
      </Corpo>
    </div>,
    document.body,
  );
}
