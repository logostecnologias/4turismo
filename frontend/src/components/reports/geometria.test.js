/**
 * Geometria do gráfico — os erros que não aparecem no navegador.
 *
 * A geometria mora num módulo sem JSX justamente para poder ser conferida aqui:
 * barra fora da área de plotagem, pilha passando do total, raio maior que a
 * própria barra e divisão por zero são coisas que renderizam sem erro e saem
 * erradas na tela.
 */
import {
  layout, caminho, tetoRedondo, MARGEM, LARGURA_VB, MAX_LARGURA_BARRA, VAO,
} from "./geometria";

const UMA = [{ chave: "conversas", rotulo: "Conversas", slot: 1 }];
const DUAS = [
  { chave: "recebidas", rotulo: "Recebidas", slot: 1 },
  { chave: "enviadas", rotulo: "Enviadas", slot: 2 },
];

function dias(n, gerar) {
  return Array.from({ length: n }, (_, i) => ({
    dia: `2026-08-${String(i + 1).padStart(2, "0")}`,
    ...gerar(i),
  }));
}

describe("tetoRedondo", () => {
  test("sobe para o próximo número redondo", () => {
    expect(tetoRedondo(5)).toBe(5);
    expect(tetoRedondo(7)).toBe(10);
    expect(tetoRedondo(120)).toBe(200);
    expect(tetoRedondo(1837)).toBe(2000);
  });

  test("nunca devolve menos que o valor", () => {
    for (const v of [1, 3, 9, 44, 101, 999, 12345]) {
      expect(tetoRedondo(v)).toBeGreaterThanOrEqual(v);
    }
  });

  test("zero não vira zero — o eixo não pode dividir por ele", () => {
    expect(tetoRedondo(0)).toBe(1);
  });
});

describe("caminho da barra", () => {
  test("começa na base, fecha, e arredonda só o topo", () => {
    const d = caminho(10, 20, 24, 100, true);
    expect(d.startsWith("M10,120")).toBe(true);
    expect(d.endsWith("Z")).toBe(true);
    expect(d).toContain("Q");
    expect(caminho(10, 20, 24, 100, false)).not.toContain("Q");
  });

  test("altura zero não desenha nada", () => {
    expect(caminho(10, 20, 24, 0, true)).toBe("");
  });

  test("o raio nunca passa de metade da largura nem da altura", () => {
    // Numa barra de 3px, raio 4 viraria uma bolha; numa de 2px de altura,
    // inverteria o desenho.
    expect(caminho(0, 0, 3, 100, true)).toContain("1.5,0");
    expect(caminho(0, 0, 24, 2, true)).toContain("Q0,0 2,0");
  });
});

describe("layout de uma série", () => {
  const g = layout(dias(30, (i) => ({ conversas: i % 7 === 0 ? 0 : 3 + i })), UMA, 180);

  test("uma coluna por dia, barra dentro do teto de 24px", () => {
    expect(g.colunas).toHaveLength(30);
    expect(g.largura).toBeLessThanOrEqual(MAX_LARGURA_BARRA);
    expect(g.largura).toBeGreaterThanOrEqual(2);
  });

  test("nenhuma barra sai da área de plotagem", () => {
    for (const c of g.colunas) {
      const p = c.partes[0];
      expect(p.y).toBeGreaterThanOrEqual(MARGEM.topo - 0.01);
      expect(p.y + p.altura).toBeLessThanOrEqual(g.y0 + 0.01);
      expect(p.altura).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeGreaterThanOrEqual(MARGEM.esq - 0.01);
      expect(p.x + p.largura).toBeLessThanOrEqual(LARGURA_VB + 0.01);
    }
  });

  test("valor zero não desenha, e valor maior sobe mais", () => {
    expect(g.colunas[0].partes[0].d).toBe("");
    expect(g.colunas[29].partes[0].altura).toBeGreaterThan(g.colunas[1].partes[0].altura);
  });

  test("a grade da base coincide com o eixo", () => {
    expect(g.ticks).toHaveLength(3);
    expect(Math.abs(g.ticks[0].y - g.y0)).toBeLessThan(0.01);
  });

  test("rótulo de dia é ralo o bastante para não borrar", () => {
    expect(g.cadaN).toBe(3);
    expect(layout(dias(90, () => ({ conversas: 1 })), UMA, 180).cadaN).toBe(8);
  });
});

describe("layout empilhado", () => {
  const g = layout(dias(10, (i) => ({ recebidas: 10 + i, enviadas: 5 + i * 2 })), DUAS, 180);

  test("a base assenta no eixo e o topo respeita a área", () => {
    for (const c of g.colunas) {
      const [baixo, topo] = c.partes;
      expect(Math.abs(baixo.y + baixo.altura - g.y0)).toBeLessThan(0.01);
      expect(topo.y).toBeGreaterThanOrEqual(MARGEM.topo - 0.01);
    }
  });

  test("o topo da pilha marca o total — o vão sai da própria barra", () => {
    for (const c of g.colunas) {
      const esperado = g.y0 - (c.total / g.teto) * g.areaA + VAO;
      expect(Math.abs(c.partes[1].y - esperado)).toBeLessThan(0.01);
    }
  });

  test("só o segmento de cima é arredondado", () => {
    for (const c of g.colunas) {
      expect(c.partes[0].arredondado).toBe(false);
      expect(c.partes[1].arredondado).toBe(true);
    }
  });

  test("o total da coluna é a soma das séries", () => {
    expect(g.colunas[0].total).toBe(15);
  });
});

describe("casos extremos", () => {
  test("um dia só: barra no teto, dentro do viewBox", () => {
    const g = layout(dias(1, () => ({ conversas: 5 })), UMA, 180);
    expect(g.largura).toBe(MAX_LARGURA_BARRA);
    expect(g.colunas[0].partes[0].x + g.largura).toBeLessThanOrEqual(LARGURA_VB);
  });

  test("90 dias: barra fina, mas nunca invisível", () => {
    const g = layout(dias(90, () => ({ conversas: 1 })), UMA, 180);
    expect(g.largura).toBeGreaterThanOrEqual(2);
    expect(g.largura).toBeLessThan(10);
  });

  test("tudo zero não divide por zero nem desenha barra", () => {
    const g = layout(dias(5, () => ({ conversas: 0 })), UMA, 180);
    expect(Number.isFinite(g.teto)).toBe(true);
    expect(g.teto).toBe(1);
    expect(g.colunas.every((c) => c.partes[0].d === "")).toBe(true);
  });

  test("segmento zerado no meio não quebra a pilha", () => {
    const g = layout(
      dias(3, (i) => ({ recebidas: i === 0 ? 100 : 0, enviadas: i === 0 ? 0 : 1 })),
      DUAS, 180
    );
    expect(g.colunas[1].partes[0].d).toBe("");
    expect(g.colunas[1].partes[1].d).not.toBe("");
  });

  test("lista vazia não explode", () => {
    expect(layout([], UMA, 180).colunas).toHaveLength(0);
  });
});
