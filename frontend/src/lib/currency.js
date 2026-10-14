/** Formata um valor em reais: 97 → "R$ 97,00". */
export function formatBRL(value) {
  return Number(value || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

/**
 * Lê o que foi digitado num campo de preço e devolve o valor em reais.
 * Considera só os dígitos, tratando os dois últimos como centavos — é a
 * digitação que o usuário espera num campo de moeda ("9700" vira 97,00) e
 * evita o "097" que aparecia quando o campo era um <input type="number">
 * começando em zero.
 */
export function parseBRLInput(text) {
  const digits = String(text).replace(/\D/g, "").slice(0, 11);
  return digits ? Number(digits) / 100 : 0;
}
