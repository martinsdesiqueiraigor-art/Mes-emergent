import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import LoginPage from "@/pages/LoginPage";
import OperatorPage from "@/pages/OperatorPage";
import SupervisorLayout from "@/pages/SupervisorLayout";
import DashboardPage from "@/pages/DashboardPage";
import ValidationQueuePage from "@/pages/ValidationQueuePage";
import OpsPage from "@/pages/OpsPage";
import MastersPage from "@/pages/MastersPage";
import ReportsPage from "@/pages/ReportsPage";
import SettingsPage from "@/pages/SettingsPage";

function Protected({ children, role }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8 text-slate-500">Carregando...</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (role && user.role !== role) return <Navigate to={user.role === "supervisor" ? "/supervisor" : "/operador"} replace />;
  return children;
}

function Root() {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8 text-slate-500">Carregando...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={user.role === "supervisor" ? "/supervisor" : "/operador"} replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <div className="App">
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/operador" element={<Protected role="operator"><OperatorPage /></Protected>} />
            <Route path="/supervisor" element={<Protected role="supervisor"><SupervisorLayout /></Protected>}>
              <Route index element={<DashboardPage />} />
              <Route path="fila" element={<ValidationQueuePage />} />
              <Route path="ops" element={<OpsPage />} />
              <Route path="cadastros" element={<MastersPage />} />
              <Route path="relatorios" element={<ReportsPage />} />
              <Route path="config" element={<SettingsPage />} />
            </Route>
            <Route path="/" element={<Root />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
        <Toaster position="top-right" richColors closeButton />
      </BrowserRouter>
    </AuthProvider>
  );
}
