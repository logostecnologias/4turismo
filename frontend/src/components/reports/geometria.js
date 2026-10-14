/**
 * Geometria das colunas do gráfico, separada da view.
 *
 * Fica em JS puro (sem JSX) de propósito: é a parte que pode errar em silêncio
 * — barra saindo da área de plotagem, pilha passando do total, raio maior que a
 * própria barra — e aqui ela pode ser conferida sem navegador.
 */

export const MAX_LARGURA_BARRA = 24;
export const RAIO = 4;
export const VAO = 2; // vão na cor da superfície entre segmentos empilhados

export const MARGEM = { topo: 12, base: 22, esq: 44 };
export const LARGURA_VB = 900;

/** Caminho de uma coluna: topo arredondado, base quadrada. */
export function caminho(x, y, largura, altura, arredondado) {
  if (altura <= 0) return "";
  // O raio nunca passa de metade da largura nem da altura: numa barra de 3px o
  // arredondamento cheio viraria uma bolha, e numa de 1px inverteria o desenho.
  const r = arredondado ? Math.min(RAIO, largura / 2, altura) : 0;
  const base = y + altura;
  if (!r) return `M${x},${y}H${x + largura}V${base}H${x}Z`;
  return `M${x},${base}V${y + r}Q${x},${y} ${x + r},${y}` +
    `H${x + largura - r}Q${x + largura},${y} ${x + largura},${y + r}` +
    `V${base}Z`;
}

/**
 * Teto do eixo em número redondo, para o rótulo não sair "1.837".
 *
 * Sobe para o próximo 1/2/5/10 da ordem de grandeza. A versão anterior usava
 * meio-passo da escala, o que funcionava para milhares (1.837 → 2.000) mas não
 * para valores de um dígito: com escala 1, o meio-passo é 0,5 e o teto de 7
 * voltava 7 — nada redondo.
 */
export function tetoRedondo(valor) {
  if (valor <= 1) return 1;
  const escala = 10 ** Math.floor(Math.log10(valor));
  for (const passo of [1, 2, 5, 10]) {
    if (passo * escala >= valor) return passo * escala;
  }
  return 10 * escala;
}

/**
 * Posições de tudo que o gráfico desenha.
 *
 * `dados` são as linhas por dia; `series` é [{chave, rotulo, slot}] na ordem de
 * empilhamento (a última fica no topo e leva o arredondamento).
 */
export function layout(dados, series, altura) {
  const areaL = LARGURA_VB - MARGEM.esq;
  const areaA = altura - MARGEM.topo - MARGEM.base;
  const passo = areaL / Math.max(1, dados.length);
  const largura = Math.min(MAX_LARGURA_BARRA, Math.max(2, passo - 6));
  const y0 = MARGEM.topo + areaA;
  const maior = Math.max(
    1,
    ...dados.map((d) => series.reduce((acc, s) => acc + (d[s.chave] || 0), 0))
  );
  const teto = tetoRedondo(maior);

  const colunas = dados.map((d, i) => {
    const x = MARGEM.esq + i * passo + (passo - largura) / 2;
    let acumulado = 0;
    const partes = series.map((s, idx) => {
      const valor = d[s.chave] || 0;
      const alturaBruta = (valor / teto) * areaA;
      // O vão é descontado da própria barra, então o topo da pilha continua
      // marcando o total: somar o vão faria a coluna crescer além do valor.
      const ultima = idx === series.length - 1;
      const alturaFinal = idx > 0 && alturaBruta > VAO ? alturaBruta - VAO : alturaBruta;
      const y = y0 - acumulado - alturaBruta + (idx > 0 && alturaBruta > VAO ? VAO : 0);
      acumulado += alturaBruta;
      return {
        chave: s.chave, slot: s.slot, rotulo: s.rotulo, valor,
        x, y, largura, altura: alturaFinal, arredondado: ultima,
        d: caminho(x, y, largura, alturaFinal, ultima),
      };
    });
    return {
      dia: d.dia,
      indice: i,
      centro: MARGEM.esq + i * passo + passo / 2,
      alvo: { x: MARGEM.esq + i * passo, largura: passo },
      total: partes.reduce((a, p) => a + p.valor, 0),
      partes,
    };
  });

  return {
    colunas, teto, passo, largura, y0, areaA,
    ticks: [0, teto / 2, teto].map((t) => ({ valor: t, y: y0 - (t / teto) * areaA })),
    // Rótulo de dia de tantos em tantos: com 90 dias, um por coluna vira borrão.
    cadaN: Math.ceil(dados.length / 12),
  };
}
