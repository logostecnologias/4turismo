import { useMemo, useState } from "react";
import { Table } from "@phosphor-icons/react";
import { layout, LARGURA_VB, MARGEM } from "./geometria";

/**
 * Colunas por dia, uma ou duas séries empilhadas.
 *
 * As cores **não são hex aqui, são variável CSS** (`--serie-1`, `--serie-2`),
 * definidas em `index.css` com um valor por tema. O SVG aceita `var()` em
 * `fill`, então o gráfico troca de paleta junto com a tela sem re-render.
 *
 * Os dois pares passaram pelo validador de paleta contra a superfície do
 * respectivo tema. Desde 14/10/2026 o par é o roxo e a laranja da marca (era `#2563EB` + `#EA580C`); o
 * escuro **não é ele clareado**: os passos claros reprovam na banda de
 * luminosidade do tema escuro, então lá são `#3B82F6` + `#E06D1F`, validados
 * como conjunto contra a `--surface` escura.
 *
 * Duas medidas de escalas diferentes (conversas e mensagens) nunca compartilham
 * o mesmo gráfico com dois eixos — são dois gráficos lado a lado. Eixo duplo
 * inventa uma correlação que não está no dado.
 */
const SERIES = {
  1: "var(--serie-1)",
  2: "var(--serie-2)",
};
const GRADE = "var(--grade)";
const EIXO = "var(--eixo)";
// Realce da faixa sob o cursor: no tema claro é uma sombra sobre branco, no
// escuro precisa ser luz sobre escuro. Uma cor fixa desapareceria num dos dois.
const REALCE = "var(--text)";

function rotuloDia(dia) {
  const [, m, d] = dia.split("-");
  return `${d}/${m}`;
}

export default function SerieDiaria({ titulo, dados, series, altura = 180 }) {
  const [sobre, setSobre] = useState(null);
  const [tabela, setTabela] = useState(false);
  const g = useMemo(() => layout(dados, series, altura), [dados, series, altura]);

  if (dados.length === 0) {
    return (
      <div className="border border-line bg-surface p-5 rounded-lg" data-testid={`serie-${titulo}`}>
        <div className="text-sm font-medium text-ink-3 mb-2">{titulo}</div>
        <div className="text-xs text-ink-4 py-8 text-center">Sem dados no período.</div>
      </div>
    );
  }

  return (
    <div className="border border-line bg-surface p-5 rounded-lg" data-testid={`serie-${titulo}`}>
      <div className="flex items-center justify-between mb-3 gap-3">
        <div className="text-sm font-medium text-ink-3">{titulo}</div>
        <div className="flex items-center gap-3">
          {/* Legenda sempre presente com duas séries ou mais: identidade nunca
              pode depender só da cor. */}
          {series.length > 1 && (
            <div className="flex items-center gap-2.5">
              {series.map((s) => (
                <span key={s.chave} className="flex items-center gap-1.5 text-[11px] text-ink-2">
                  <span className="w-2.5 h-2.5 shrink-0" style={{ background: SERIES[s.slot] }} />
                  {s.rotulo}
                </span>
              ))}
            </div>
          )}
          <button onClick={() => setTabela((v) => !v)}
            title={tabela ? "Ver gráfico" : "Ver tabela"}
            className={`p-1 hover:bg-surface-3 ${tabela ? "text-ink bg-surface-2" : "text-ink-3"}`}
            data-testid={`tabela-${titulo}`}>
            <Table size={13} weight="regular" />
          </button>
        </div>
      </div>

      {tabela ? (
        <div className="max-h-[164px] overflow-y-auto border border-line rounded-lg">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-2">
              <tr className="border-b border-line">
                <th className="text-left px-3 py-2.5.5 text-sm font-medium text-ink-3">Dia</th>
                {series.map((s) => (
                  <th key={s.chave} className="text-right px-3 py-2.5.5 text-sm font-medium text-ink-3">
                    {s.rotulo}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dados.map((d) => (
                <tr key={d.dia} className="border-b border-line last:border-b-0">
                  <td className="px-3 py-2.5.5 tabular-nums">{rotuloDia(d.dia)}</td>
                  {series.map((s) => (
                    <td key={s.chave} className="px-3 py-2.5.5 text-right tabular-nums">{d[s.chave] || 0}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${LARGURA_VB} ${altura}`} className="w-full" style={{ height: altura }}
            role="img" aria-label={titulo}>
            {g.ticks.map((t) => (
              <g key={t.valor}>
                {/* Grade fina, sólida e discreta — tracejado só acrescenta ruído. */}
                <line x1={MARGEM.esq} x2={LARGURA_VB} y1={t.y} y2={t.y} stroke={GRADE} strokeWidth="1" />
                <text x={MARGEM.esq - 8} y={t.y + 3.5} textAnchor="end"
                  className="fill-ink-4" style={{ fontSize: 10 }}>
                  {Math.round(t.valor).toLocaleString("pt-BR")}
                </text>
              </g>
            ))}

            {g.colunas.map((c) => (
              <g key={c.dia}
                onMouseEnter={() => setSobre(c)}
                onMouseLeave={() => setSobre(null)}>
                {/* Alvo de mouse do tamanho da faixa, não da barra: coluna fina
                    não pode exigir precisão de mira. */}
                <rect x={c.alvo.x} y={MARGEM.topo} width={c.alvo.largura} height={g.areaA}
                  fill={REALCE} fillOpacity={sobre?.indice === c.indice ? 0.06 : 0} />
                {c.partes.map((p) => (
                  p.d ? <path key={p.chave} d={p.d} fill={SERIES[p.slot]} /> : null
                ))}
              </g>
            ))}

            <line x1={MARGEM.esq} x2={LARGURA_VB} y1={g.y0} y2={g.y0} stroke={EIXO} strokeWidth="1" />

            {g.colunas.map((c) => (c.indice % g.cadaN === 0 ? (
              <text key={c.dia} x={c.centro} y={altura - 6} textAnchor="middle"
                className="fill-ink-4" style={{ fontSize: 10 }}>
                {rotuloDia(c.dia)}
              </text>
            ) : null))}
          </svg>

          {sobre && (
            <div className="absolute pointer-events-none border border-line-strong bg-surface px-2 py-1 z-10 rounded-lg"
              style={{
                left: `${(sobre.centro / LARGURA_VB) * 100}%`,
                top: 0,
                transform: "translate(-50%, -100%)",
              }}
              data-testid="serie-tooltip">
              <div className="text-sm font-medium text-ink-3">
                {rotuloDia(sobre.dia)}
              </div>
              {sobre.partes.map((p) => (
                <div key={p.chave} className="flex items-center gap-1.5 text-xs whitespace-nowrap">
                  <span className="w-2 h-2 shrink-0" style={{ background: SERIES[p.slot] }} />
                  <span className="text-ink-2">{p.rotulo}</span>
                  <span className="tabular-nums font-bold ml-auto">{p.valor}</span>
                </div>
              ))}
              {sobre.partes.length > 1 && (
                <div className="text-[10px] tabular-nums text-ink-3 border-t border-line mt-0.5 pt-0.5">
                  total {sobre.total}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export { SERIES };
