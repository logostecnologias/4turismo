import { NOME_PRODUTO } from "@/lib/marca";

// Logo e nome do produto, num lugar só (barra lateral e login).
//
// A logo é o SVG vetorizado em `public/logo-4turismo.svg` (o mesmo arquivo é o
// favicon), servido como imagem: as cores dela são da marca, não da paleta da
// agência, e por isso não passam por token nenhum.
//
// Há duas: a original, para fundo claro, e `logo-4turismo-escuro.svg`, com a
// faceta escura clareada, porque sobre o fundo noite ela some e o 4 perde o
// lado esquerdo. `fundo="auto"` segue o tema (as duas vão no HTML e o CSS do
// `.dark` escolhe, sem esperar o React); `fundo="escuro"` é para lugar escuro
// em qualquer tema, como o painel do login.
const BASE = process.env.PUBLIC_URL || "";
const LOGO = `${BASE}/logo-4turismo.svg`;
const LOGO_ESCURO = `${BASE}/logo-4turismo-escuro.svg`;

const TAMANHOS = {
  sm: { logo: "w-9 h-9", nome: "text-xl" },
  md: { logo: "w-10 h-10", nome: "text-2xl" },
  lg: { logo: "w-16 h-16", nome: "text-4xl" },
};

export function Monograma({ tamanho = "sm", fundo = "auto" }) {
  const classe = `${TAMANHOS[tamanho].logo} shrink-0`;
  if (fundo === "escuro") {
    return <img src={LOGO_ESCURO} alt="" aria-hidden="true" className={classe} />;
  }
  return (
    <>
      <img src={LOGO} alt="" aria-hidden="true" className={`${classe} dark:hidden`} />
      <img src={LOGO_ESCURO} alt="" aria-hidden="true" className={`${classe} hidden dark:block`} />
    </>
  );
}

export default function Marca({ tamanho = "sm", fundo = "auto", children }) {
  const t = TAMANHOS[tamanho];
  return (
    <div className="flex items-center gap-2.5">
      <Monograma tamanho={tamanho} fundo={fundo} />
      <div>
        <div className={`font-heading font-bold ${t.nome} leading-none`}>{NOME_PRODUTO}</div>
        {children}
      </div>
    </div>
  );
}
