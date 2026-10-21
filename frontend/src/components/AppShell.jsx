import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useUnreadNotifier } from "@/hooks/useUnreadNotifier";
import { useAtividadesAtrasadas } from "@/hooks/useAtividadesAtrasadas";
import BotaoTema from "@/components/BotaoTema";
import Marca from "@/components/Marca";
import {
  House, CalendarBlank, ChatCircle, ChatsCircle, FlowArrow, Sparkle, User,
  FileText, CheckCircle, ChartBar, WhatsappLogo, SquaresFour, Users,
  Gear, Buildings, CreditCard, ChartLine, SignOut, ArrowUUpLeft, Globe,
  MapTrifold, Van, SlidersHorizontal,
} from "@phosphor-icons/react";

// A barra lateral e uma lista de grupos, nao uma lista de itens: com 15 telas
// em sequencia, "Departamentos" e "Funil" ficavam vizinhos sem nada em comum, e
// achar uma tela era ler a lista inteira. O grupo sem titulo existe para o
// Painel, que nao pertence a nenhum assunto.
const adminNav = [
  {
    itens: [
      { to: "/admin", icon: ChartLine, label: "Visão geral", end: true, testid: "métricas-globais" },
      { to: "/admin/tenants", icon: Buildings, label: "Agências", testid: "empresas" },
      { to: "/admin/plans", icon: CreditCard, label: "Planos" },
    ],
  },
];

function tenantNav(role) {
  // `testid` guarda o identificador antigo onde o rotulo mudou para o
  // vocabulario de clinica (08/10/2026): o data-testid saia do rotulo, e
  // trocar o texto nao pode quebrar quem localiza o item por ele.
  // Fora do bloco de admin de proposito: quem atende o telefone marca,
  // remarca e cancela o dia inteiro.
  const saidas = { to: "/app/agenda", icon: CalendarBlank, label: "Saídas", testid: "agenda" };
  const atendimento = {
    titulo: "Atendimento",
    itens: [
      { to: "/app/inbox", icon: ChatCircle, label: "Atendimento" },
      { to: "/app/atendimento", icon: ChatsCircle, label: "Conversas" },
    ],
  };
  // O que a agencia vende e como opera. Passeios, equipe e regras eram abas
  // escondidas dentro de Saidas (21/10/2026): a agencia so descobria que tinha
  // de configura-los clicando nelas. Aqui ficam a vista, ao lado das Saidas.
  const operacao = {
    titulo: "Passeios",
    testid: "operacao",
    itens: [
      saidas,
      { to: "/app/passeios", icon: MapTrifold, label: "Passeios", testid: "passeios" },
      { to: "/app/equipe", icon: Van, label: "Equipe e veículos", testid: "equipe" },
      { to: "/app/reservas-embarque", icon: SlidersHorizontal, label: "Reservas e embarque", testid: "reservas-embarque" },
    ],
  };

  // "Hoje" e a tela inicial de todo mundo, atendente inclusive: a agenda do
  // dia e a fila sao o que a recepcao olha primeiro (`backend/hoje.py`).
  const hoje = { to: "/app", icon: House, label: "Hoje", end: true, testid: "painel" };

  // Atendente ve um grupo so, e titulo de grupo unico e ruido: fica sem titulo.
  if (role !== "tenant_admin") return [{ itens: [hoje, saidas, ...atendimento.itens] }];

  return [
    { itens: [hoje] },
    operacao,
    atendimento,
    {
      titulo: "Automação",
      itens: [
        { to: "/app/bots", icon: FlowArrow, label: "Fluxos automáticos", testid: "robôs" },
        { to: "/app/ai-agents", icon: Sparkle, label: "Assistente de IA", testid: "agentes-de-ia" },
      ],
    },
    {
      titulo: "Clientes",
      testid: "pacientes",
      itens: [
        { to: "/app/contacts", icon: User, label: "Clientes", testid: "contatos" },
        { to: "/app/funil", icon: FileText, label: "Orçamentos", testid: "funil" },
        { to: "/app/atividades", icon: CheckCircle, label: "Tarefas", testid: "atividades" },
      ],
    },
    {
      titulo: "Análise",
      itens: [{ to: "/app/relatorios", icon: ChartBar, label: "Relatórios" }],
    },
    {
      titulo: "Configuração",
      itens: [
        { to: "/app/whatsapp", icon: WhatsappLogo, label: "WhatsApp" },
        { to: "/app/site", icon: Globe, label: "Site" },
        { to: "/app/departments", icon: SquaresFour, label: "Departamentos" },
        { to: "/app/users", icon: Users, label: "Usuários" },
        { to: "/app/settings", icon: Gear, label: "Ajustes" },
      ],
    },
  ];
}

export default function AppShell({ variant = "tenant" }) {
  const { user, logout, impersonatedFrom, exitImpersonation } = useAuth();
  const navigate = useNavigate();
  const nav = variant === "admin" ? adminNav : tenantNav(user?.role);
  const { aguardando } = useUnreadNotifier(variant === "tenant");
  const atrasadas = useAtividadesAtrasadas(variant === "tenant" && user?.role === "tenant_admin");

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const handleExit = async () => {
    await exitImpersonation();
    navigate("/admin/tenants");
  };

  return (
    <div className="h-dvh overflow-hidden flex flex-col bg-canvas print:h-auto print:overflow-visible print:block">
      {impersonatedFrom === "super_admin" && variant === "tenant" && (
        <div className="w-full bg-warning-solid border-b border-line px-6 py-2 flex items-center justify-between text-warning-fg print:hidden" data-testid="impersonation-bar">
          <div className="text-sm font-semibold">
            Modo suporte: você está navegando como admin desta empresa
          </div>
          <button data-testid="exit-impersonation" onClick={handleExit} className="zz-btn zz-btn-secondary text-xs">
            <ArrowUUpLeft size={14} weight="regular" /> Sair do modo suporte
          </button>
        </div>
      )}

      <div className="flex flex-1 min-h-0 print:block">
        <aside className="w-60 border-r border-line bg-surface flex flex-col print:hidden" data-testid="sidebar">
          <div className="px-5 py-6 border-b border-line">
            <Marca>
              <div className="text-xs text-ink-3 font-medium mt-0.5">
                {variant === "admin" ? "Super Admin" : (user?.role === "agent" ? "Atendente" : "Admin")}
              </div>
            </Marca>
          </div>

          <nav className="flex-1 py-3 overflow-y-auto">
            {nav.map((grupo, i) => (
              <div key={grupo.titulo || `grupo-${i}`} className={i > 0 ? "mt-4" : ""}>
                {grupo.titulo && (
                  <div
                    className="zz-nav-grupo"
                    data-testid={`nav-grupo-${grupo.testid || grupo.titulo.toLowerCase().replace(/\s/g, "-")}`}
                  >
                    {grupo.titulo}
                  </div>
                )}
                {grupo.itens.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    data-testid={`nav-${item.testid || item.label.toLowerCase().replace(/\s/g, "-")}`}
                    className={({ isActive }) => `zz-nav-item ${isActive ? "active" : ""}`}
                  >
                    {/* Peso fino, como na prévia aprovada: o `regular` com traço mais
                        grosso pesava a barra inteira. */}
                    <item.icon size={19} weight="light" />
                    <span>{item.label}</span>
                    {item.to === "/app/atividades" && atrasadas > 0 && (
                      <span
                        className="zz-queue-badge ml-auto"
                        data-testid="nav-atividades-badge"
                        title={`${atrasadas} atividade(s) atrasada(s)`}
                      >
                        {atrasadas > 99 ? "99+" : atrasadas}
                      </span>
                    )}
                    {item.to === "/app/inbox" && aguardando > 0 && (
                      <span
                        className="zz-queue-badge zz-alert-pulse ml-auto"
                        data-testid="nav-queue-badge"
                        title={`${aguardando} conversa(s) aguardando atendimento`}
                      >
                        {aguardando > 99 ? "99+" : aguardando}
                      </span>
                    )}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>

          <div className="border-t border-line p-3">
            <div className="text-xs text-ink-3 mb-2 truncate">{user?.email}</div>
            <div className="flex items-center gap-2">
              <button data-testid="logout-btn" onClick={handleLogout} className="zz-btn zz-btn-secondary flex-1 justify-center">
                <SignOut size={16} weight="regular" /> Sair
              </button>
              <BotaoTema />
            </div>
          </div>
        </aside>

        <main className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden print:overflow-visible">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
