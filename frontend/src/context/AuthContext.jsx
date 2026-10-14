import { createContext, useContext, useEffect, useState } from "react";
import api from "@/lib/api";
import { sincronizar } from "@/lib/tema";

const AuthContext = createContext(null);

function readCookie(name) {
  const m = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return m ? decodeURIComponent(m[2]) : null;
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [impersonatedFrom, setImpersonatedFrom] = useState(null); // "super_admin" if active

  const refresh = () => {
    return api
      .get("/auth/me")
      .then((r) => {
        sincronizar(r.data);
        setUser(r.data);
        setImpersonatedFrom(readCookie("impersonated_role"));
      })
      .catch(() => setUser(false));
  };

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, []);

  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    sincronizar(data);
    setUser(data);
    setImpersonatedFrom(null);
    return data;
  };

  const logout = async () => {
    try { await api.post("/auth/logout"); } catch {}
    setUser(false);
    setImpersonatedFrom(null);
  };

  const exitImpersonation = async () => {
    await api.post("/auth/exit-impersonation");
    setImpersonatedFrom(null);
    await refresh();
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, exitImpersonation, impersonatedFrom }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
