/**
 * Paletas da agência: cor e tipografia andam juntas.
 *
 * Os valores de cor moram em `index.css` (um bloco `:root[data-paleta]` por
 * paleta); aqui fica o que a tela de Ajustes precisa para mostrar a escolha e o
 * endereço das fontes de cada uma. A lista de ids tem de bater com `PALETAS`
 * em `backend/aparencia.py` (quem recusa um id estranho) e com `sites.PALETAS`
 * (as cores do site público da agência).
 *
 * `amostra` são as três cores do cartão de escolha (marca, detalhe, fundo),
 * no tema claro: é como a paleta é reconhecida de relance.
 */
const G = "https://fonts.googleapis.com/css2?display=swap&family=";

export const PALETA_PADRAO = "oficial";

export const PALETAS = [
  {
    id: "oficial", nome: "4turismo", resumo: "Lagoa, areia e sol, a paleta da marca",
    titulo: "Bricolage Grotesque", corpo: "Inter", serifa: false, peso: 700,
    amostra: ["#0B6E8A", "#16B3AC", "#F5B841"],
    // Já importada em index.css: é a paleta padrão.
    fontes: null,
  },
  {
    id: "sol", nome: "Pôr do sol", resumo: "Coral do entardecer com areia quente",
    titulo: "Sora", corpo: "DM Sans", serifa: false, peso: 600,
    amostra: ["#BD3F0C", "#F2B544", "#F8F3EF"],
    fontes: G + "Sora:wght@500;600;700&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700",
  },
  {
    id: "azulejo", nome: "Azulejo", resumo: "Azul dos azulejos de São Luís",
    titulo: "Lora", corpo: "Source Sans 3", serifa: true, peso: 600,
    amostra: ["#1F4E8C", "#E8C35A", "#F1F3F6"],
    fontes: G + "Lora:wght@500;600;700&family=Source+Sans+3:wght@400;500;600;700",
  },
];

export const paletaPorId = (id) => PALETAS.find((p) => p.id === id);
