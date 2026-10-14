import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

export default function ProtectedRoute({ children, roles }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-ink-2">
        Carregando...
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  const allowed = Array.isArray(roles) ? roles : (roles ? [roles] : null);
  if (allowed && !allowed.includes(user.role)) {
    return <Navigate to={user.role === "super_admin" ? "/admin" : "/app"} replace />;
  }
  return children;
}
