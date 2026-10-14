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
    id: "lagoa", nome: "Lagoa", resumo: "Turquesa das lagoas com areia",
    titulo: "Plus Jakarta Sans", corpo: "Plus Jakarta Sans", serifa: false, peso: 700,
    amostra: ["#0D767F", "#D9B98A", "#F7F5F0"],
    fontes: G + "Plus+Jakarta+Sans:wght@400;500;600;700",
  },
  {
    id: "duna", nome: "Duna", resumo: "Ocre das dunas com azul do céu",
    titulo: "Bricolage Grotesque", corpo: "Figtree", serifa: false, peso: 600,
    amostra: ["#8C5A1E", "#5FA8C9", "#F8F4EF"],
    fontes: G + "Bricolage+Grotesque:opsz,wght@12..96,500;12..96,600;12..96,700&family=Figtree:wght@400;500;600;700",
  },
  {
    id: "sol", nome: "Pôr do sol", resumo: "Coral do entardecer com areia quente",
    titulo: "Sora", corpo: "DM Sans", serifa: false, peso: 600,
    amostra: ["#BD3F0C", "#F2B544", "#F8F3EF"],
    fontes: G + "Sora:wght@500;600;700&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700",
  },
  {
    id: "mangue", nome: "Mangue", resumo: "Verde do mangue com barro",
    titulo: "Manrope", corpo: "Manrope", serifa: false, peso: 700,
    amostra: ["#2E6B3F", "#A0754D", "#F4F5F2"],
    fontes: G + "Manrope:wght@400;500;600;700;800",
  },
  {
    id: "azulejo", nome: "Azulejo", resumo: "Azul dos azulejos de São Luís",
    titulo: "Lora", corpo: "Source Sans 3", serifa: true, peso: 600,
    amostra: ["#1F4E8C", "#E8C35A", "#F1F3F6"],
    fontes: G + "Lora:wght@500;600;700&family=Source+Sans+3:wght@400;500;600;700",
  },
  {
    id: "caju", nome: "Caju", resumo: "Vermelho do caju com castanha",
    titulo: "Fraunces", corpo: "Figtree", serifa: true, peso: 600,
    amostra: ["#9B2335", "#C79A5B", "#F7F3F1"],
    fontes: G + "Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Figtree:wght@400;500;600;700",
  },
];

export const paletaPorId = (id) => PALETAS.find((p) => p.id === id);
