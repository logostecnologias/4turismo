import { Suspense, lazy } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import { Toaster } from "@/components/ui/sonner";
import AppShell from "@/components/AppShell";
import LoginPage from "@/pages/Login";
import TenantDashboard from "@/pages/tenant/TenantDashboard";
import BotsPage from "@/pages/tenant/BotsPage";
import AIAgentsPage from "@/pages/tenant/AIAgentsPage";
import ContactsPage from "@/pages/tenant/ContactsPage";
import FunilPage from "@/pages/tenant/FunilPage";
import AtividadesPage from "@/pages/tenant/AtividadesPage";
import AgendaPage from "@/pages/tenant/AgendaPage";
import ListaEmbarquePage from "@/pages/tenant/ListaEmbarquePage";
import PasseiosPage from "@/pages/tenant/PasseiosPage";
import EquipePage from "@/pages/tenant/EquipePage";
import RegrasReservasPage from "@/pages/tenant/RegrasReservasPage";
import RelatoriosPage from "@/pages/tenant/RelatoriosPage";
import InboxPage from "@/pages/tenant/InboxPage";
import WhatsAppPage from "@/pages/tenant/WhatsAppPage";
import SettingsPage from "@/pages/tenant/SettingsPage";
import AtendimentoPage from "@/pages/tenant/AtendimentoPage";
import DepartamentosPage from "@/pages/tenant/DepartamentosPage";
import UsersPage from "@/pages/tenant/UsersPage";
import SitePage from "@/pages/tenant/SitePage";
import GuiasPage from "@/pages/tenant/GuiasPage";
import DesempenhoSitePage from "@/pages/tenant/DesempenhoSitePage";
import SuperAdminDashboard from "@/pages/superadmin/SuperAdminDashboard";
import TenantsPage from "@/pages/superadmin/TenantsPage";
import PlansPage from "@/pages/superadmin/PlansPage";
import "@/App.css";

// O editor de fluxos carrega o React Flow, de longe a dependência mais pesada
// do frontend, e é a única tela que precisa dele. Estático, ele entrava no
// bundle inicial de todo atendente que só usa a Central e nunca abre o editor.
const FlowEditorPage = lazy(() => import("@/pages/tenant/FlowEditorPage"));

function EditorLoading() {
  return (
    <div className="h-screen flex items-center justify-center" data-testid="editor-loading">
      <div className="text-sm font-medium text-ink-3">
        Carregando editor…
      </div>
    </div>
  );
}

const TENANT_ROLES = ["tenant_admin", "agent"];
const ADMIN_ONLY = ["tenant_admin"];

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          {/* Tenant + Agent shell */}
          <Route
            path="/app"
            element={
              <ProtectedRoute roles={TENANT_ROLES}>
                <AppShell variant="tenant" />
              </ProtectedRoute>
            }
          >
            <Route index element={<TenantDashboard />} />
            <Route path="atendimento" element={<AtendimentoPage />} />
            <Route path="bots" element={<ProtectedRoute roles={ADMIN_ONLY}><BotsPage /></ProtectedRoute>} />
            <Route path="ai-agents" element={<ProtectedRoute roles={ADMIN_ONLY}><AIAgentsPage /></ProtectedRoute>} />
            <Route path="whatsapp" element={<ProtectedRoute roles={ADMIN_ONLY}><WhatsAppPage /></ProtectedRoute>} />
            <Route path="departments" element={<ProtectedRoute roles={ADMIN_ONLY}><DepartamentosPage /></ProtectedRoute>} />
            <Route path="users" element={<ProtectedRoute roles={ADMIN_ONLY}><UsersPage /></ProtectedRoute>} />
            <Route path="site" element={<ProtectedRoute roles={ADMIN_ONLY}><SitePage /></ProtectedRoute>} />
            <Route path="site/guias" element={<ProtectedRoute roles={ADMIN_ONLY}><GuiasPage /></ProtectedRoute>} />
            <Route path="desempenho-site" element={<ProtectedRoute roles={ADMIN_ONLY}><DesempenhoSitePage /></ProtectedRoute>} />
            <Route path="contacts" element={<ProtectedRoute roles={ADMIN_ONLY}><ContactsPage /></ProtectedRoute>} />
            <Route path="funil" element={<ProtectedRoute roles={ADMIN_ONLY}><FunilPage /></ProtectedRoute>} />
            <Route path="atividades" element={<ProtectedRoute roles={ADMIN_ONLY}><AtividadesPage /></ProtectedRoute>} />
            <Route path="relatorios" element={<ProtectedRoute roles={ADMIN_ONLY}><RelatoriosPage /></ProtectedRoute>} />
            <Route path="agenda" element={<AgendaPage />} />
            <Route path="agenda/embarque" element={<ListaEmbarquePage />} />
            <Route path="passeios" element={<ProtectedRoute roles={ADMIN_ONLY}><PasseiosPage /></ProtectedRoute>} />
            <Route path="equipe" element={<ProtectedRoute roles={ADMIN_ONLY}><EquipePage /></ProtectedRoute>} />
            <Route path="reservas-embarque" element={<ProtectedRoute roles={ADMIN_ONLY}><RegrasReservasPage /></ProtectedRoute>} />
            <Route path="inbox" element={<InboxPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>

          <Route
            path="/app/bots/:botId/flow/:flowId"
            element={
              <ProtectedRoute roles={ADMIN_ONLY}>
                <Suspense fallback={<EditorLoading />}>
                  <FlowEditorPage />
                </Suspense>
              </ProtectedRoute>
            }
          />

          <Route
            path="/admin"
            element={
              <ProtectedRoute roles={["super_admin"]}>
                <AppShell variant="admin" />
              </ProtectedRoute>
            }
          >
            <Route index element={<SuperAdminDashboard />} />
            <Route path="tenants" element={<TenantsPage />} />
            <Route path="plans" element={<PlansPage />} />
          </Route>

          <Route path="/" element={<Navigate to="/login" replace />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </BrowserRouter>
      <Toaster />
    </AuthProvider>
  );
}

export default App;
