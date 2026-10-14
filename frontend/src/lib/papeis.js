// Rótulo em português para o papel do usuário.
//
// O valor gravado (`tenant_admin`, `agent`, `super_admin`) é chave interna e
// continua sendo o que trafega na API. Ele nunca deve chegar à tela: "tenant"
// é vocabulário de banco de dados, não palavra que um cliente reconheça.
const ROTULOS = {
  tenant_admin: "Admin",
  agent: "Atendente",
  super_admin: "Super Admin",
};

export function rotuloPapel(role) {
  return ROTULOS[role] || role || "—";
}
