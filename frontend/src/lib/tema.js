/**
 * Tema claro/escuro.
 *
 * Três estados, não dois: `claro`, `escuro` e `sistema`. "Sistema" é o padrão —
 * quem já configurou o sistema operacional no escuro não deveria ter que
 * configurar de novo aqui, e uma escolha explícita tem que ganhar do sistema
 * depois, senão o botão não funcionaria para quem está no escuro e quer claro.
 *
 * `aplicar()` roda **antes do React montar** (chamada em `index.js`), não num
 * `useEffect`: no efeito, o primeiro quadro já teria sido pintado com o tema
 * claro e a tela daria um flash branco a cada carregamento no tema escuro.
 *
 * Duas escolhas, guardadas em lugares diferentes do banco:
 *
 * - **claro/escuro é do usuário** (`users.preferencias.modo`): cada pessoa da
 *   equipe escolhe o seu, e ele a acompanha em qualquer computador.
 * - **a paleta é da empresa** (`tenants.aparencia.paleta`): é a identidade da
 *   agência, a mesma para todo mundo dela, e só o admin troca.
 *
 * O `localStorage` continua existindo como **cópia** do que veio do servidor,
 * não como fonte: é o que permite pintar o primeiro quadro certo antes de
 * `/auth/me` responder. Quando a resposta chega, `sincronizar()` corrige a
 * cópia se ela divergir (outro computador, admin que trocou a paleta).
 */
import { PALETA_PADRAO, paletaPorId } from "@/lib/paletas";

const CHAVE = "4turismo:tema";
const CHAVE_PALETA = "4turismo:paleta";
export const TEMAS = ["claro", "escuro", "sistema"];
export const EVENTO = "4turismo:tema";

const consulta = () =>
  typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

/** O que está guardado, ou `sistema` se não houver nada (ou valor estranho). */
export function preferencia() {
  try {
    const v = localStorage.getItem(CHAVE);
    return TEMAS.includes(v) ? v : "sistema";
  } catch {
    // Navegador com armazenamento bloqueado (janela privada, política de site):
    // o tema segue o sistema em vez de estourar antes de a tela abrir.
    return "sistema";
  }
}

/** O tema que está de fato pintado na tela: só `claro` ou `escuro`. */
export function efetivo(pref = preferencia()) {
  if (pref === "sistema") return consulta()?.matches ? "escuro" : "claro";
  return pref;
}

/** Põe (ou tira) a classe `dark` no `<html>`, que é onde o Tailwind a procura. */
export function aplicar(pref = preferencia()) {
  const tema = efetivo(pref);
  document.documentElement.classList.toggle("dark", tema === "escuro");
  return tema;
}

export function guardar(pref) {
  try {
    localStorage.setItem(CHAVE, pref);
  } catch {
    // Sem armazenamento a escolha vale só para esta aba — melhor que recusar
    // a troca de tema.
  }
  const tema = aplicar(pref);
  // O botão de tema guarda o estado atual para escolher o ícone. Quando a
  // troca vem de fora dele (o `/auth/me` trazendo a escolha feita noutro
  // computador), é este aviso que o faz redesenhar.
  window.dispatchEvent(new Event(EVENTO));
  return tema;
}

// ---------------------------------------------------------------------------
// Paleta da empresa
// ---------------------------------------------------------------------------

/** Paleta guardada neste navegador, ou a padrão. */
export function paletaGuardada() {
  try {
    const v = localStorage.getItem(CHAVE_PALETA);
    return paletaPorId(v) ? v : PALETA_PADRAO;
  } catch {
    return PALETA_PADRAO;
  }
}

/**
 * Põe o `<link>` das fontes da paleta no `<head>`, uma vez por paleta. A padrão
 * não tem link: as fontes dela já vêm de `index.css`.
 */
export function carregarFontes(id) {
  const p = paletaPorId(id);
  if (!p?.fontes || typeof document === "undefined") return;
  const marca = `fontes-${p.id}`;
  if (document.getElementById(marca)) return;
  const link = document.createElement("link");
  link.id = marca;
  link.rel = "stylesheet";
  link.href = p.fontes;
  document.head.appendChild(link);
}

/** Liga a paleta no `<html>` (o CSS a procura em `data-paleta`). */
export function aplicarPaleta(id = paletaGuardada()) {
  const valida = paletaPorId(id) ? id : PALETA_PADRAO;
  carregarFontes(valida);
  const raiz = document.documentElement;
  if (valida === PALETA_PADRAO) raiz.removeAttribute("data-paleta");
  else raiz.setAttribute("data-paleta", valida);
  return valida;
}

export function guardarPaleta(id) {
  const valida = paletaPorId(id) ? id : PALETA_PADRAO;
  try {
    localStorage.setItem(CHAVE_PALETA, valida);
  } catch {
    // Sem armazenamento a paleta ainda vale nesta aba; só o próximo
    // carregamento abre com a padrão por um instante até o `/auth/me`.
  }
  return aplicarPaleta(valida);
}

/**
 * Aplica o que veio do servidor (`/auth/me` e `/auth/login`). Super admin não
 * tem empresa, então `paleta` vem nula e vale a padrão. `modo` nulo quer dizer
 * que a pessoa nunca escolheu: mantém o que este navegador já tinha, em vez de
 * apagar uma escolha local feita antes de a preferência ir para o banco.
 */
export function sincronizar({ paleta, modo } = {}) {
  guardarPaleta(paleta || PALETA_PADRAO);
  if (TEMAS.includes(modo) && modo !== preferencia()) guardar(modo);
}

/**
 * Avisa quando o sistema troca de tema. Só tem efeito com a preferência em
 * `sistema`: com escolha explícita, o tema do sistema é irrelevante e reagir a
 * ele desfaria a escolha da pessoa.
 */
export function observarSistema(callback) {
  const mq = consulta();
  if (!mq) return () => {};
  const ouvinte = () => {
    if (preferencia() === "sistema") callback(aplicar("sistema"));
  };
  mq.addEventListener("change", ouvinte);
  return () => mq.removeEventListener("change", ouvinte);
}
