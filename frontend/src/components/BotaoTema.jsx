import { useEffect, useState } from "react";
import { Sun, Moon, Desktop } from "@phosphor-icons/react";
import { preferencia, guardar, observarSistema, EVENTO } from "@/lib/tema";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

/**
 * Alterna claro → escuro → sistema num clique só.
 *
 * Um botão que cicla, e não um menu: são três estados e o rótulo do próximo já
 * cabe no `title`. Um dropdown para isto custaria um clique a mais em todo uso.
 *
 * O ícone mostra o estado **atual**, não o próximo — o botão é também o
 * indicador de qual tema está valendo.
 */
const SEQUENCIA = { claro: "escuro", escuro: "sistema", sistema: "claro" };
const ROTULO = { claro: "Tema claro", escuro: "Tema escuro", sistema: "Tema do sistema" };
const ICONE = { claro: Sun, escuro: Moon, sistema: Desktop };

export default function BotaoTema({ className = "" }) {
  const [pref, setPref] = useState(preferencia);
  // O botão também aparece no login, antes de haver sessão: ali a escolha fica
  // só neste navegador, e vai para o banco no próximo clique já logado.
  const { user } = useAuth() || {};

  // Com a preferência em "sistema", quem manda é o sistema operacional: se ele
  // trocar com a tela aberta, a tela troca junto.
  useEffect(() => observarSistema(() => setPref(preferencia())), []);

  // A preferência também pode mudar por fora do botão: o `/auth/me` traz a
  // escolha feita noutro computador.
  useEffect(() => {
    const ouvir = () => setPref(preferencia());
    window.addEventListener(EVENTO, ouvir);
    return () => window.removeEventListener(EVENTO, ouvir);
  }, []);

  const trocar = () => {
    const proximo = SEQUENCIA[pref] || "claro";
    guardar(proximo);
    setPref(proximo);
    // A tela troca na hora; a gravação vai depois e sem bloquear. Se falhar, o
    // tema continua valendo neste navegador, que é o que importa no momento.
    if (user) api.patch("/settings/preferencias", { modo: proximo }).catch(() => {});
  };

  const Icone = ICONE[pref] || Sun;
  return (
    <button
      type="button"
      onClick={trocar}
      className={`zz-btn-icon shrink-0 ${className}`}
      title={`${ROTULO[pref]} — clique para ${ROTULO[SEQUENCIA[pref]].toLowerCase()}`}
      aria-label={ROTULO[pref]}
      data-testid="botao-tema"
      data-tema={pref}
    >
      <Icone size={16} weight="regular" />
    </button>
  );
}
