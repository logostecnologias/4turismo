import { useEffect, useState } from "react";
import api from "@/lib/api";
import { ChartLine, Buildings, Users, Robot, ChatCircleDots } from "@phosphor-icons/react";

const items = [
  { key: "tenants_total", label: "Agências cadastradas", icon: Buildings },
  { key: "tenants_active", label: "Agências ativas", icon: Buildings },
  { key: "users_total", label: "Admins de agência", icon: Users },
  { key: "bots_total", label: "Fluxos automáticos no sistema", icon: Robot },
  { key: "conversations_total", label: "Conversas totais", icon: ChatCircleDots },
  { key: "messages_total", label: "Mensagens totais", icon: ChatCircleDots },
];

export default function SuperAdminDashboard() {
  const [m, setM] = useState(null);
  useEffect(() => { api.get("/admin/metrics").then((r) => setM(r.data)); }, []);

  return (
    <div className="zz-page" data-testid="admin-dashboard">
      <div className="mb-8">
        <h1 className="font-heading text-4xl font-semibold">Visão geral</h1>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 border border-line bg-surface rounded-lg">
        {items.map((it) => {
          const Icon = it.icon;
          return (
            <div key={it.key} className="border-r border-b border-line p-6 last:border-r-0" data-testid={`admin-metric-${it.key}`}>
              <div className="flex items-center justify-between mb-4">
                <div className="text-sm font-medium text-ink-3">{it.label}</div>
                <div className="w-9 h-9 flex items-center justify-center rounded-full bg-brand-soft text-brand">
                  <Icon size={16} weight="regular" />
                </div>
              </div>
              <div className="font-heading text-5xl font-semibold">{m ? m[it.key] : "—"}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
