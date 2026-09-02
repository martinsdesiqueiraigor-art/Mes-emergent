import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Factory } from "lucide-react";

export default function LoginPage() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={user.role === "supervisor" ? "/supervisor" : "/operador"} replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const u = await login(username.trim(), password);
      toast.success(`Bem-vindo, ${u.name}`);
      nav(u.role === "supervisor" ? "/supervisor" : "/operador");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Falha ao entrar");
    } finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-md">
        <div className="flex items-center gap-3 mb-8 justify-center">
          <div className="w-12 h-12 rounded-lg bg-slate-900 flex items-center justify-center">
            <Factory className="text-emerald-400" size={26} />
          </div>
          <div>
            <div className="font-black text-2xl tracking-tight">ProFab</div>
            <div className="text-xs uppercase tracking-widest text-slate-500">MES / APS</div>
          </div>
        </div>
        <Card className="border-slate-200 shadow-sm">
          <CardHeader>
            <CardTitle>Entrar no sistema</CardTitle>
            <CardDescription>Chão de fábrica e supervisão</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-4">
              <div>
                <Label htmlFor="u">Usuário</Label>
                <Input id="u" data-testid="login-username-input" value={username} onChange={(e) => setU(e.target.value)}
                  autoFocus autoComplete="username" />
              </div>
              <div>
                <Label htmlFor="p">Senha</Label>
                <Input id="p" data-testid="login-password-input" type="password" value={password}
                  onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
              </div>
              <Button data-testid="login-submit-btn" disabled={busy} className="w-full touch-btn bg-slate-900 hover:bg-slate-800">
                {busy ? "Entrando..." : "Entrar"}
              </Button>
            </form>
            <div className="mt-6 pt-4 border-t text-xs text-slate-500">
              <div className="font-semibold text-slate-600 mb-1">Contas demo</div>
              <div>Supervisor: <code className="mono">admin / admin123</code></div>
              <div>Operador: <code className="mono">op1 / op123</code></div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
