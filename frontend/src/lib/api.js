import axios from "axios";

export const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export const api = axios.create({ baseURL: API });

api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("token");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

export const fmt = {
  hms: (secs) => {
    if (!secs || secs < 0) secs = 0;
    const s = Math.floor(secs);
    const hh = String(Math.floor(s / 3600)).padStart(2, "0");
    const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${hh}:${mm}:${ss}`;
  },
  date: (iso) => {
    if (!iso) return "-";
    try { return new Date(iso).toLocaleDateString("pt-BR"); } catch { return iso; }
  },
  datetime: (iso) => {
    if (!iso) return "-";
    try { return new Date(iso).toLocaleString("pt-BR"); } catch { return iso; }
  },
  pct: (v) => `${((v || 0) * 100).toFixed(1)}%`,
};
