import { useEffect, useState } from "react";

// Campo de hora no formato `00:00`, digitado, não `<input type="time">` nem
// `<input type="number">`.
//
// Os dois nativos são controles do navegador: ignoram os tokens de cor do
// produto, mudam de desenho entre navegadores, e desenham por cima do que
// estiver à direita do campo (foi assim que as setinhas do `number` cobriram o
// sufixo "min" na configuração da agenda). Aqui é um campo de texto comum, com
// máscara e validação por regex.
//
// Dois usos, e a diferença entre eles importa:
//
//   - **Relógio** (`CampoHora`), na grade de atendimento: 00:00 a 23:59. Como
//     nenhuma hora começa com dígito acima de 2, o campo completa sozinho
//     digitar 9 mostra "09" na hora, e quem tecla 9-3-0 para 09:30 nunca vê
//     "93:0" no meio do caminho.
//
//   - **Duração** (`CampoDuracao`), na configuração: 00:00 a 99:59, porque
//     "48 horas de antecedência" é um valor legítimo. Aqui 4 pode mesmo ser a
//     dezena das horas, então **não** há como completar sozinho: o campo enche
//     da esquerda para a direita e o `00:00` do placeholder é que ensina.
//
// A duração é guardada em minutos e convertida na borda. O backend continua
// recebendo o que sempre recebeu (minutos em `intervalo_min`, horas em
// `antecedencia_min_horas` e `lembrete_horas`), quem converte é a tela, porque
// é ela que resolveu mostrar 01:30 em vez de "1,5 horas".

export const REGEX_RELOGIO = /^([01]\d|2[0-3]):[0-5]\d$/;
export const REGEX_DURACAO = /^\d{2}:[0-5]\d$/;

const MAX_HORAS_RELOGIO = 23;
const MAX_HORAS_DURACAO = 99;

function digitos(texto, autoPrefixo) {
  let d = (texto || "").replace(/\D/g, "");
  if (autoPrefixo && d && d[0] > "2") d = `0${d}`;
  return d.slice(0, 4);
}

function mascara(texto, autoPrefixo) {
  const d = digitos(texto, autoPrefixo);
  return d.length <= 2 ? d : `${d.slice(0, 2)}:${d.slice(2)}`;
}

/** Completa o que foi digitado, ou `null` se não der para ler como hora.
 *
 * A leitura acompanha o que o campo mostra: enche da esquerda, hora e depois
 * minuto. Três dígitos aparecem como "18:3" e viram 18:30, lê-los como 01:83
 * faria o valor gravado contradizer o que estava na tela. */
function completa(texto, autoPrefixo, maxHoras) {
  const d = digitos(texto, autoPrefixo);
  if (!d) return null;
  const horas = Number(d.slice(0, 2));
  const minutos = d.length <= 2 ? 0 : Number(`${d.slice(2)}0`.slice(0, 2));
  if (horas > maxHoras || minutos > 59) return null;
  return `${String(horas).padStart(2, "0")}:${String(minutos).padStart(2, "0")}`;
}

function Base({ valor, onChange, autoPrefixo, maxHoras, testid, largura }) {
  const [rascunho, setRascunho] = useState(valor || "");
  // O valor muda por fora (copiar o dia anterior, abrir outro profissional, um
  // limite que travou o número). Sem isto o campo seguiria mostrando o rascunho.
  useEffect(() => setRascunho(valor || ""), [valor]);

  const digitar = (bruto) => {
    const texto = mascara(bruto, autoPrefixo);
    setRascunho(texto);
    // Só entrega quando o valor está inteiro: comprometer "09:3" faria a grade
    // mudar embaixo de quem ainda está digitando.
    if (texto.length === 5) {
      const pronto = completa(texto, autoPrefixo, maxHoras);
      if (pronto) onChange(pronto);
    }
  };

  const sair = () => {
    const pronto = completa(rascunho, autoPrefixo, maxHoras);
    if (pronto) {
      setRascunho(pronto);
      if (pronto !== valor) onChange(pronto);
    } else {
      // Incompleto ou fora da faixa volta ao valor anterior, em vez de gravar
      // lixo: uma janela com hora pela metade sumiria da grade sem dizer por quê.
      setRascunho(valor || "");
    }
  };

  return (
    <input
      className={`zz-input tabular-nums text-center ${largura}`}
      inputMode="numeric"
      maxLength={5}
      placeholder="00:00"
      value={rascunho}
      data-testid={testid}
      onChange={(e) => digitar(e.target.value)}
      onBlur={sair}
    />
  );
}

/** Hora do dia, de 00:00 a 23:59, como texto "HH:MM". */
export function CampoHora({ valor, onChange, testid }) {
  return (
    <Base valor={valor} onChange={onChange} autoPrefixo maxHoras={MAX_HORAS_RELOGIO}
      testid={testid} largura="max-w-[86px]" />
  );
}

export function deMinutos(total) {
  const n = Math.max(0, Math.round(Number(total) || 0));
  return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
}

export function paraMinutos(hhmm) {
  const [h, m] = String(hhmm || "0:0").split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

/** Minutos em horas, para os campos que o backend guarda em horas.
 *
 * Arredondado na terceira casa: `5999 / 60` dá 99.98333333333333, e é esse
 * número que iria para o banco. Três casas sobrevivem à volta (99.983 × 60
 * arredonda de novo para 5999) e não deixam cauda de float no documento. */
export function emHoras(minutos) {
  return Math.round((minutos / 60) * 1000) / 1000;
}

/** Duração, de 00:00 a 99:59, medida e devolvida em **minutos**.
 *
 * `min`/`max` também são minutos e são aplicados aqui, na tela. Sem eles um
 * "24000" digitado sem querer chegava ao backend e voltava como um 422 do
 * pydantic: "Input should be less than or equal to 168", em inglês, na cara do
 * cliente. Travar aqui mostra o limite no próprio campo, no ato. */
export function CampoDuracao({ minutos, onChange, min = 0, max = 5999, testid }) {
  return (
    <Base
      valor={deMinutos(minutos)}
      maxHoras={MAX_HORAS_DURACAO}
      testid={testid}
      largura="max-w-[110px]"
      onChange={(hhmm) => onChange(Math.min(max, Math.max(min, paraMinutos(hhmm))))}
    />
  );
}
