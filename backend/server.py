"""ProFab MES/APS - Backend (FastAPI + MongoDB)"""
from __future__ import annotations

import io
import csv
import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Any, Annotated

import bcrypt
import jwt as pyjwt
import requests
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Header, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, ConfigDict, BeforeValidator
from starlette.middleware.cors import CORSMiddleware
from openpyxl import Workbook

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("profab")

# --- config ---
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALG = "HS256"
JWT_TTL_HOURS = 24
APP_NAME = os.environ.get("APP_NAME", "profab-mes")

STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")

# --- db ---
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

app = FastAPI(title="ProFab MES/APS")
api = APIRouter(prefix="/api")
security = HTTPBearer(auto_error=False)


# ---------- Storage helpers ----------
_storage_key: Optional[str] = None


def init_storage(force: bool = False) -> Optional[str]:
    global _storage_key
    if _storage_key and not force:
        return _storage_key
    if not EMERGENT_KEY:
        logger.warning("EMERGENT_LLM_KEY missing; storage disabled")
        return None
    try:
        r = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY}, timeout=30)
        r.raise_for_status()
        _storage_key = r.json()["storage_key"]
        return _storage_key
    except Exception as e:
        logger.error(f"Storage init failed: {e}")
        return None


def put_object(path: str, data: bytes, content_type: str) -> dict:
    key = init_storage()
    if not key:
        raise HTTPException(status_code=503, detail="Armazenamento indisponível")
    r = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    if r.status_code in (403, 404):
        init_storage(force=True)
        key = _storage_key
        r = requests.put(
            f"{STORAGE_URL}/objects/{path}",
            headers={"X-Storage-Key": key, "Content-Type": content_type},
            data=data,
            timeout=120,
        )
    r.raise_for_status()
    return r.json()


def get_object(path: str) -> tuple[bytes, str]:
    key = init_storage()
    if not key:
        raise HTTPException(status_code=503, detail="Armazenamento indisponível")
    r = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key}, timeout=60)
    if r.status_code in (403, 404):
        init_storage(force=True)
        key = _storage_key
        r = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key}, timeout=60)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "application/octet-stream")


# ---------- helpers ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def clean(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return doc
    doc.pop("_id", None)
    return doc


def hash_pw(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_pw(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except Exception:
        return False


def make_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_TTL_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


async def get_current_user(cred: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    if not cred:
        raise HTTPException(status_code=401, detail="Não autenticado")
    try:
        payload = pyjwt.decode(cred.credentials, JWT_SECRET, algorithms=[JWT_ALG])
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido")
    user = await db.users.find_one({"id": payload["sub"], "active": True})
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    return clean(user)


async def require_supervisor(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "supervisor":
        raise HTTPException(status_code=403, detail="Requer perfil supervisor")
    return user


async def audit(user_id: str, action: str, entity: str, entity_id: str, meta: dict | None = None):
    await db.audit_log.insert_one({
        "id": new_id(),
        "user_id": user_id,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "meta": meta or {},
        "at": now_iso(),
    })


# ---------- Models ----------
class LoginIn(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    name: str
    password: str
    role: str = "operator"  # operator|supervisor


class UserOut(BaseModel):
    id: str
    username: str
    name: str
    role: str
    active: bool = True


class PasswordReset(BaseModel):
    user_id: str
    new_password: str


class MachineIn(BaseModel):
    code: str
    name: str
    oee_target: float = 0.75


class DrawingIn(BaseModel):
    code: str
    name: str
    description: str = ""
    file_id: Optional[str] = None


class OperationIn(BaseModel):
    seq: int
    name: str
    machine_id: str
    cycle_time_sec: float = 60.0  # ideal time per piece
    qty_target: int = 0


class OpIn(BaseModel):
    number: str
    client: str
    drawing_id: str
    qty_planned: int
    due_date: str  # ISO date
    operations: list[OperationIn] = []
    priority: int = 3


class StartApontIn(BaseModel):
    op_id: str
    operation_seq: int
    machine_id: str


class UpdateApontIn(BaseModel):
    good_delta: int = 0
    scrap_delta: int = 0
    scrap_reason: Optional[str] = None
    justification: Optional[str] = None


class StoppageIn(BaseModel):
    reason: str
    comment: Optional[str] = None


class StoppageEndIn(BaseModel):
    comment: Optional[str] = None


class CloseApontIn(BaseModel):
    note: Optional[str] = None


class ApproveIn(BaseModel):
    approve: bool
    note: Optional[str] = None
    adjust_good: Optional[int] = None
    adjust_scrap: Optional[int] = None


class ReopenOpIn(BaseModel):
    reason: str


# ---------- Seed on startup ----------
STOPPAGE_REASONS_DEFAULT = [
    "Manutenção Corretiva",
    "Falta de Matéria-Prima",
    "Setup/Troca de Ferramenta",
    "Aguardando Inspeção PCP",
    "Queda de Energia",
    "Refeição/Intervalo",
]

SCRAP_REASONS_DEFAULT = [
    "Defeito de Usinagem",
    "Riscado / Amassado",
    "Fora de Tolerância",
    "Erro de Setup",
    "Material Defeituoso",
]


@app.on_event("startup")
async def startup():
    init_storage()
    # ensure default admin/operator
    if not await db.users.find_one({"username": "admin"}):
        await db.users.insert_one({
            "id": new_id(),
            "username": "admin",
            "name": "Supervisor Padrão",
            "password_hash": hash_pw("admin123"),
            "role": "supervisor",
            "active": True,
            "created_at": now_iso(),
        })
    if not await db.users.find_one({"username": "op1"}):
        await db.users.insert_one({
            "id": new_id(),
            "username": "op1",
            "name": "Operador Demo",
            "password_hash": hash_pw("op123"),
            "role": "operator",
            "active": True,
            "created_at": now_iso(),
        })
    # settings
    if not await db.settings.find_one({"id": "global"}):
        await db.settings.insert_one({
            "id": "global",
            "shift_end_time": "22:00",
            "stoppage_comment_min_minutes": 15,
            "backup_retention_days": 30,
            "stoppage_reasons": STOPPAGE_REASONS_DEFAULT,
            "scrap_reasons": SCRAP_REASONS_DEFAULT,
        })
    logger.info("Startup ok")


@app.on_event("shutdown")
async def shutdown():
    client.close()


# ---------- Auth ----------
@api.post("/auth/login")
async def login(inp: LoginIn):
    u = await db.users.find_one({"username": inp.username, "active": True})
    if not u or not verify_pw(inp.password, u["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    token = make_token(u["id"], u["role"])
    return {
        "token": token,
        "user": {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"]},
    }


@api.get("/auth/me")
async def me(user=Depends(get_current_user)):
    return {"id": user["id"], "username": user["username"], "name": user["name"], "role": user["role"]}


# ---------- Users / Operators ----------
@api.get("/users")
async def list_users(user=Depends(require_supervisor), role: Optional[str] = None):
    q: dict = {"active": True}
    if role:
        q["role"] = role
    users = await db.users.find(q, {"password_hash": 0}).to_list(500)
    return [clean(u) for u in users]


@api.post("/users")
async def create_user(inp: UserCreate, user=Depends(require_supervisor)):
    if await db.users.find_one({"username": inp.username}):
        raise HTTPException(400, "Usuário já existe")
    doc = {
        "id": new_id(),
        "username": inp.username,
        "name": inp.name,
        "password_hash": hash_pw(inp.password),
        "role": inp.role,
        "active": True,
        "created_at": now_iso(),
    }
    await db.users.insert_one(doc)
    await audit(user["id"], "create", "user", doc["id"], {"username": inp.username})
    doc.pop("password_hash")
    return clean(doc)


@api.put("/users/{uid}")
async def update_user(uid: str, inp: UserCreate, user=Depends(require_supervisor)):
    upd = {"username": inp.username, "name": inp.name, "role": inp.role}
    r = await db.users.update_one({"id": uid}, {"$set": upd})
    if not r.matched_count:
        raise HTTPException(404, "Usuário não encontrado")
    await audit(user["id"], "update", "user", uid, upd)
    return {"ok": True}


@api.post("/users/reset-password")
async def reset_pw(inp: PasswordReset, user=Depends(require_supervisor)):
    r = await db.users.update_one({"id": inp.user_id}, {"$set": {"password_hash": hash_pw(inp.new_password)}})
    if not r.matched_count:
        raise HTTPException(404, "Usuário não encontrado")
    await audit(user["id"], "reset_password", "user", inp.user_id)
    return {"ok": True}


@api.delete("/users/{uid}")
async def del_user(uid: str, user=Depends(require_supervisor)):
    r = await db.users.update_one({"id": uid}, {"$set": {"active": False}})
    if not r.matched_count:
        raise HTTPException(404)
    await audit(user["id"], "soft_delete", "user", uid)
    return {"ok": True}


# ---------- Machines ----------
@api.get("/machines")
async def list_machines(user=Depends(get_current_user)):
    rows = await db.machines.find({"active": True}).to_list(500)
    return [clean(r) for r in rows]


@api.post("/machines")
async def create_machine(inp: MachineIn, user=Depends(require_supervisor)):
    doc = {"id": new_id(), "active": True, "created_at": now_iso(), **inp.model_dump()}
    await db.machines.insert_one(doc)
    await audit(user["id"], "create", "machine", doc["id"])
    return clean(doc)


@api.put("/machines/{mid}")
async def update_machine(mid: str, inp: MachineIn, user=Depends(require_supervisor)):
    r = await db.machines.update_one({"id": mid}, {"$set": inp.model_dump()})
    if not r.matched_count:
        raise HTTPException(404)
    await audit(user["id"], "update", "machine", mid)
    return {"ok": True}


@api.delete("/machines/{mid}")
async def del_machine(mid: str, user=Depends(require_supervisor)):
    await db.machines.update_one({"id": mid}, {"$set": {"active": False}})
    await audit(user["id"], "soft_delete", "machine", mid)
    return {"ok": True}


# ---------- Drawings ----------
@api.get("/drawings")
async def list_drawings(user=Depends(get_current_user)):
    rows = await db.drawings.find({"active": True}).to_list(500)
    return [clean(r) for r in rows]


@api.post("/drawings")
async def create_drawing(inp: DrawingIn, user=Depends(require_supervisor)):
    doc = {"id": new_id(), "active": True, "created_at": now_iso(), **inp.model_dump()}
    await db.drawings.insert_one(doc)
    await audit(user["id"], "create", "drawing", doc["id"])
    return clean(doc)


@api.put("/drawings/{did}")
async def update_drawing(did: str, inp: DrawingIn, user=Depends(require_supervisor)):
    r = await db.drawings.update_one({"id": did}, {"$set": inp.model_dump()})
    if not r.matched_count:
        raise HTTPException(404)
    await audit(user["id"], "update", "drawing", did)
    return {"ok": True}


@api.delete("/drawings/{did}")
async def del_drawing(did: str, user=Depends(require_supervisor)):
    await db.drawings.update_one({"id": did}, {"$set": {"active": False}})
    await audit(user["id"], "soft_delete", "drawing", did)
    return {"ok": True}


# ---------- File uploads (drawings) ----------
@api.post("/uploads")
async def upload_file(file: UploadFile = File(...), user=Depends(require_supervisor)):
    ext = (file.filename or "bin").rsplit(".", 1)[-1] if "." in (file.filename or "") else "bin"
    file_id = new_id()
    path = f"{APP_NAME}/drawings/{user['id']}/{file_id}.{ext}"
    data = await file.read()
    result = put_object(path, data, file.content_type or "application/octet-stream")
    doc = {
        "id": file_id,
        "storage_path": result["path"],
        "original_filename": file.filename,
        "content_type": file.content_type or "application/octet-stream",
        "size": result.get("size", len(data)),
        "is_deleted": False,
        "uploaded_by": user["id"],
        "created_at": now_iso(),
    }
    await db.files.insert_one(doc)
    return {"id": file_id, "filename": file.filename, "size": doc["size"]}


@api.get("/uploads/{fid}/download")
async def download_file(fid: str, authorization: Optional[str] = Header(None), auth: Optional[str] = Query(None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif auth:
        token = auth
    if not token:
        raise HTTPException(401, "Não autenticado")
    try:
        pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except pyjwt.PyJWTError:
        raise HTTPException(401, "Token inválido")
    rec = await db.files.find_one({"id": fid, "is_deleted": False})
    if not rec:
        raise HTTPException(404, "Arquivo não encontrado")
    data, ct = get_object(rec["storage_path"])
    return Response(content=data, media_type=rec.get("content_type", ct))


# ---------- Production Orders (OPs) ----------
@api.get("/ops")
async def list_ops(
    user=Depends(get_current_user),
    status: Optional[str] = None,
    client_filter: Optional[str] = Query(None, alias="client"),
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    query: dict = {}
    if status:
        query["status"] = status
    if client_filter:
        query["client"] = {"$regex": client_filter, "$options": "i"}
    if q:
        query["$or"] = [
            {"number": {"$regex": q, "$options": "i"}},
            {"client": {"$regex": q, "$options": "i"}},
        ]
    total = await db.ops.count_documents(query)
    rows = (
        await db.ops.find(query)
        .sort("due_date", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    return {"total": total, "items": [clean(r) for r in rows]}


@api.get("/ops/{oid}")
async def get_op(oid: str, user=Depends(get_current_user)):
    r = await db.ops.find_one({"id": oid})
    if not r:
        raise HTTPException(404)
    return clean(r)


@api.post("/ops")
async def create_op(inp: OpIn, user=Depends(require_supervisor)):
    doc = {
        "id": new_id(),
        "status": "planned",  # planned|in_progress|completed|cancelled
        "qty_good": 0,
        "qty_scrap": 0,
        "created_at": now_iso(),
        **inp.model_dump(),
    }
    await db.ops.insert_one(doc)
    await audit(user["id"], "create", "op", doc["id"], {"number": inp.number})
    return clean(doc)


@api.put("/ops/{oid}")
async def update_op(oid: str, inp: OpIn, user=Depends(require_supervisor)):
    r = await db.ops.update_one({"id": oid}, {"$set": inp.model_dump()})
    if not r.matched_count:
        raise HTTPException(404)
    await audit(user["id"], "update", "op", oid)
    return {"ok": True}


@api.post("/ops/{oid}/cancel")
async def cancel_op(oid: str, inp: ReopenOpIn, user=Depends(require_supervisor)):
    r = await db.ops.update_one({"id": oid}, {"$set": {"status": "cancelled", "cancel_reason": inp.reason}})
    if not r.matched_count:
        raise HTTPException(404)
    await audit(user["id"], "cancel", "op", oid, {"reason": inp.reason})
    return {"ok": True}


@api.post("/ops/{oid}/reopen")
async def reopen_op(oid: str, inp: ReopenOpIn, user=Depends(require_supervisor)):
    r = await db.ops.update_one(
        {"id": oid, "status": {"$in": ["completed", "cancelled"]}},
        {"$set": {"status": "in_progress", "reopen_reason": inp.reason}},
    )
    if not r.matched_count:
        raise HTTPException(400, "OP não pode ser reaberta")
    await audit(user["id"], "reopen", "op", oid, {"reason": inp.reason})
    return {"ok": True}


# ---------- Apontamentos ----------
def _compute_open_seconds(a: dict) -> int:
    started = datetime.fromisoformat(a["started_at"])
    end = datetime.fromisoformat(a["closed_at"]) if a.get("closed_at") else datetime.now(timezone.utc)
    total = (end - started).total_seconds()
    stopped = 0.0
    for s in a.get("stoppages", []):
        s_start = datetime.fromisoformat(s["started_at"])
        s_end = datetime.fromisoformat(s["ended_at"]) if s.get("ended_at") else datetime.now(timezone.utc)
        stopped += (s_end - s_start).total_seconds()
    return int(max(0, total - stopped))


@api.get("/apontamentos")
async def list_ap(
    user=Depends(get_current_user),
    status: Optional[str] = None,
    op_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    operator_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
):
    q: dict = {}
    if status:
        q["status"] = status
    if op_id:
        q["op_id"] = op_id
    if machine_id:
        q["machine_id"] = machine_id
    if operator_id:
        q["operator_ids"] = operator_id
    if date_from or date_to:
        rng: dict = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        q["started_at"] = rng

    total = await db.apontamentos.count_documents(q)
    rows = (
        await db.apontamentos.find(q)
        .sort("started_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    for r in rows:
        r["elapsed_seconds"] = _compute_open_seconds(r)
    return {"total": total, "items": [clean(r) for r in rows]}


@api.get("/apontamentos/{aid}")
async def get_ap(aid: str, user=Depends(get_current_user)):
    r = await db.apontamentos.find_one({"id": aid})
    if not r:
        raise HTTPException(404)
    r["elapsed_seconds"] = _compute_open_seconds(r)
    return clean(r)


@api.post("/apontamentos/start")
async def start_ap(inp: StartApontIn, user=Depends(get_current_user)):
    # find or create consolidated apontamento for op/operation/machine currently open
    existing = await db.apontamentos.find_one({
        "op_id": inp.op_id,
        "operation_seq": inp.operation_seq,
        "machine_id": inp.machine_id,
        "status": {"$in": ["running", "stopped"]},
    })
    if existing:
        if user["id"] not in existing.get("operator_ids", []):
            await db.apontamentos.update_one(
                {"id": existing["id"]},
                {"$addToSet": {"operator_ids": user["id"]}},
            )
        await audit(user["id"], "join", "apontamento", existing["id"])
        return {"id": existing["id"], "joined": True}

    op = await db.ops.find_one({"id": inp.op_id})
    if not op:
        raise HTTPException(404, "OP não encontrada")
    doc = {
        "id": new_id(),
        "op_id": inp.op_id,
        "op_number": op["number"],
        "operation_seq": inp.operation_seq,
        "machine_id": inp.machine_id,
        "operator_ids": [user["id"]],
        "status": "running",  # running|stopped|closed|pending_validation|validated|rejected
        "good": 0,
        "scrap": 0,
        "stoppages": [],  # {id, reason, started_at, ended_at, comment}
        "auto_closed": False,
        "notes": [],
        "started_at": now_iso(),
    }
    await db.apontamentos.insert_one(doc)
    await db.ops.update_one({"id": inp.op_id, "status": "planned"}, {"$set": {"status": "in_progress"}})
    await audit(user["id"], "start", "apontamento", doc["id"])
    return {"id": doc["id"], "joined": False}


@api.post("/apontamentos/{aid}/update")
async def update_ap(aid: str, inp: UpdateApontIn, user=Depends(get_current_user)):
    a = await db.apontamentos.find_one({"id": aid})
    if not a:
        raise HTTPException(404)
    if a["status"] not in ("running", "stopped"):
        raise HTTPException(400, "Apontamento não está em andamento")

    op = await db.ops.find_one({"id": a["op_id"]})
    total_planned = op["qty_planned"]
    total_produced = op.get("qty_good", 0) + op.get("qty_scrap", 0)
    residual = total_planned - total_produced

    new_scrap = a["scrap"] + inp.scrap_delta
    upd = {"scrap": new_scrap, "good": a["good"] + inp.good_delta}
    needs_approval = False
    if inp.scrap_delta > 0:
        if inp.scrap_delta > residual and not inp.justification:
            raise HTTPException(400, "Sucata acima do saldo residual: justificativa obrigatória")
        if inp.scrap_delta > residual:
            needs_approval = True
            upd["needs_scrap_approval"] = True
            upd["scrap_justification"] = inp.justification
            upd["scrap_reason"] = inp.scrap_reason

    await db.apontamentos.update_one({"id": aid}, {"$set": upd})
    await audit(user["id"], "update", "apontamento", aid, {"good_delta": inp.good_delta, "scrap_delta": inp.scrap_delta})
    return {"ok": True, "needs_approval": needs_approval}


@api.post("/apontamentos/{aid}/stoppage/start")
async def stoppage_start(aid: str, inp: StoppageIn, user=Depends(get_current_user)):
    a = await db.apontamentos.find_one({"id": aid})
    if not a:
        raise HTTPException(404)
    if a["status"] != "running":
        raise HTTPException(400, "Apontamento não está em execução")
    stop = {"id": new_id(), "reason": inp.reason, "started_at": now_iso(), "ended_at": None, "comment": inp.comment}
    await db.apontamentos.update_one(
        {"id": aid},
        {"$set": {"status": "stopped"}, "$push": {"stoppages": stop}},
    )
    await audit(user["id"], "stoppage_start", "apontamento", aid, {"reason": inp.reason})
    return {"ok": True, "stoppage_id": stop["id"]}


@api.post("/apontamentos/{aid}/stoppage/end")
async def stoppage_end(aid: str, inp: StoppageEndIn, user=Depends(get_current_user)):
    a = await db.apontamentos.find_one({"id": aid})
    if not a:
        raise HTTPException(404)
    if a["status"] != "stopped":
        raise HTTPException(400, "Não há parada ativa")
    stoppages = a["stoppages"]
    if not stoppages:
        raise HTTPException(400, "Sem paradas")
    settings = await db.settings.find_one({"id": "global"})
    min_min = settings.get("stoppage_comment_min_minutes", 15) if settings else 15
    idx = None
    for i in range(len(stoppages) - 1, -1, -1):
        if not stoppages[i].get("ended_at"):
            idx = i
            break
    if idx is None:
        raise HTTPException(400, "Sem parada aberta")
    end = now_iso()
    duration_min = (
        datetime.fromisoformat(end) - datetime.fromisoformat(stoppages[idx]["started_at"])
    ).total_seconds() / 60
    if duration_min >= min_min and not inp.comment:
        raise HTTPException(400, f"Parada acima de {min_min}min exige comentário")
    stoppages[idx]["ended_at"] = end
    if inp.comment:
        stoppages[idx]["comment"] = inp.comment
    await db.apontamentos.update_one({"id": aid}, {"$set": {"stoppages": stoppages, "status": "running"}})
    await audit(user["id"], "stoppage_end", "apontamento", aid, {"duration_min": round(duration_min, 1)})
    return {"ok": True}


@api.post("/apontamentos/{aid}/close")
async def close_ap(aid: str, inp: CloseApontIn, user=Depends(get_current_user)):
    a = await db.apontamentos.find_one({"id": aid})
    if not a:
        raise HTTPException(404)
    if a["status"] not in ("running", "stopped"):
        raise HTTPException(400, "Apontamento não pode ser fechado")
    # close open stoppages
    stoppages = a["stoppages"]
    end = now_iso()
    for s in stoppages:
        if not s.get("ended_at"):
            s["ended_at"] = end
    new_status = "pending_validation" if a.get("needs_scrap_approval") else "closed"
    upd = {
        "status": new_status,
        "closed_at": end,
        "closed_by": user["id"],
        "stoppages": stoppages,
    }
    if inp.note:
        upd["notes"] = a.get("notes", []) + [{"by": user["id"], "at": end, "text": inp.note}]
    await db.apontamentos.update_one({"id": aid}, {"$set": upd})

    if new_status == "closed":
        await db.ops.update_one({"id": a["op_id"]}, {"$inc": {"qty_good": a["good"], "qty_scrap": a["scrap"]}})
    await audit(user["id"], "close", "apontamento", aid)
    return {"ok": True, "status": new_status}


@api.post("/apontamentos/{aid}/approve")
async def approve_ap(aid: str, inp: ApproveIn, user=Depends(require_supervisor)):
    a = await db.apontamentos.find_one({"id": aid})
    if not a:
        raise HTTPException(404)
    if a["status"] not in ("pending_validation",):
        raise HTTPException(400, "Não está pendente de validação")
    upd: dict = {}
    good = a["good"] if inp.adjust_good is None else inp.adjust_good
    scrap = a["scrap"] if inp.adjust_scrap is None else inp.adjust_scrap
    if inp.approve:
        upd = {"status": "validated", "good": good, "scrap": scrap, "validated_by": user["id"], "validated_at": now_iso(),
               "needs_scrap_approval": False}
        await db.apontamentos.update_one({"id": aid}, {"$set": upd})
        await db.ops.update_one({"id": a["op_id"]}, {"$inc": {"qty_good": good, "qty_scrap": scrap}})
    else:
        upd = {"status": "rejected", "validated_by": user["id"], "validated_at": now_iso(),
               "reject_note": inp.note or ""}
        await db.apontamentos.update_one({"id": aid}, {"$set": upd})
    await audit(user["id"], "approve" if inp.approve else "reject", "apontamento", aid, {"note": inp.note})
    return {"ok": True}


@api.post("/apontamentos/auto-close")
async def auto_close(user=Depends(require_supervisor)):
    """Force-close all open apontamentos, mark as pending validation."""
    end = now_iso()
    rows = await db.apontamentos.find({"status": {"$in": ["running", "stopped"]}}).to_list(1000)
    for r in rows:
        stoppages = r["stoppages"]
        for s in stoppages:
            if not s.get("ended_at"):
                s["ended_at"] = end
        await db.apontamentos.update_one(
            {"id": r["id"]},
            {"$set": {
                "status": "pending_validation",
                "closed_at": end,
                "closed_by": user["id"],
                "auto_closed": True,
                "stoppages": stoppages,
            }},
        )
        await audit(user["id"], "auto_close", "apontamento", r["id"])
    return {"closed": len(rows)}


# ---------- Dashboard & Reports ----------
@api.get("/dashboard/summary")
async def dashboard_summary(user=Depends(get_current_user)):
    active = await db.apontamentos.find({"status": {"$in": ["running", "stopped"]}}).to_list(500)
    pending = await db.apontamentos.count_documents({"status": "pending_validation"})
    ops_active = await db.ops.count_documents({"status": "in_progress"})
    now = datetime.now(timezone.utc)
    overdue = 0
    ops = await db.ops.find({"status": {"$in": ["planned", "in_progress"]}}).to_list(1000)
    for o in ops:
        try:
            due = datetime.fromisoformat(o["due_date"])
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due < now:
                overdue += 1
        except Exception:
            pass

    # build machine matrix
    machines = await db.machines.find({"active": True}).to_list(500)
    users_map = {u["id"]: u["name"] for u in await db.users.find({}, {"password_hash": 0}).to_list(1000)}
    op_map = {o["id"]: o for o in ops}
    matrix = []
    active_by_machine = {a["machine_id"]: a for a in active}
    for m in machines:
        a = active_by_machine.get(m["id"])
        entry = {
            "machine_id": m["id"],
            "machine_name": m["name"],
            "machine_code": m["code"],
            "status": "idle",
            "op_number": None,
            "operators": [],
            "good": 0,
            "scrap": 0,
            "elapsed_seconds": 0,
        }
        if a:
            entry.update({
                "status": a["status"],
                "op_number": a.get("op_number"),
                "operators": [users_map.get(oid, oid) for oid in a.get("operator_ids", [])],
                "good": a["good"],
                "scrap": a["scrap"],
                "elapsed_seconds": _compute_open_seconds(a),
                "apontamento_id": a["id"],
            })
        matrix.append(entry)

    return {
        "active_ops": ops_active,
        "pending_validations": pending,
        "overdue_ops": overdue,
        "active_apontamentos": len(active),
        "machine_matrix": matrix,
    }


def _filter_apontamentos_query(date_from, date_to, machine_id, operator_id, op_id):
    q: dict = {"status": {"$in": ["closed", "validated"]}}
    if date_from or date_to:
        r: dict = {}
        if date_from:
            r["$gte"] = date_from
        if date_to:
            r["$lte"] = date_to
        q["started_at"] = r
    if machine_id:
        q["machine_id"] = machine_id
    if operator_id:
        q["operator_ids"] = operator_id
    if op_id:
        q["op_id"] = op_id
    return q


@api.get("/reports/oee")
async def report_oee(
    user=Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    machine_id: Optional[str] = None,
):
    q = _filter_apontamentos_query(date_from, date_to, machine_id, None, None)
    rows = await db.apontamentos.find(q).to_list(5000)
    ops_by_id = {o["id"]: o for o in await db.ops.find().to_list(2000)}
    machines = await db.machines.find({"active": True}).to_list(500)
    m_names = {m["id"]: m["name"] for m in machines}

    by_machine: dict = {}
    for r in rows:
        m = r["machine_id"]
        entry = by_machine.setdefault(m, {"machine_id": m, "machine_name": m_names.get(m, m),
                                           "run_seconds": 0, "stop_seconds": 0, "good": 0, "scrap": 0,
                                           "ideal_seconds": 0})
        started = datetime.fromisoformat(r["started_at"])
        ended = datetime.fromisoformat(r["closed_at"]) if r.get("closed_at") else datetime.now(timezone.utc)
        total = (ended - started).total_seconds()
        stopped = 0.0
        for s in r.get("stoppages", []):
            s_end = datetime.fromisoformat(s["ended_at"]) if s.get("ended_at") else ended
            stopped += (s_end - datetime.fromisoformat(s["started_at"])).total_seconds()
        entry["run_seconds"] += max(0, total - stopped)
        entry["stop_seconds"] += stopped
        entry["good"] += r["good"]
        entry["scrap"] += r["scrap"]
        op = ops_by_id.get(r["op_id"])
        cycle = 60.0
        if op:
            for oper in op.get("operations", []):
                if oper.get("seq") == r["operation_seq"]:
                    cycle = float(oper.get("cycle_time_sec") or 60.0)
                    break
        entry["ideal_seconds"] += (r["good"] + r["scrap"]) * cycle

    result = []
    for e in by_machine.values():
        total_time = e["run_seconds"] + e["stop_seconds"] or 1
        availability = e["run_seconds"] / total_time
        performance = min(1.0, e["ideal_seconds"] / e["run_seconds"]) if e["run_seconds"] > 0 else 0
        total_pieces = e["good"] + e["scrap"] or 1
        quality = e["good"] / total_pieces
        oee = availability * performance * quality
        result.append({
            **e,
            "availability": round(availability, 3),
            "performance": round(performance, 3),
            "quality": round(quality, 3),
            "oee": round(oee, 3),
        })
    return result


@api.get("/reports/productivity")
async def report_productivity(
    user=Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    operator_id: Optional[str] = None,
):
    q = _filter_apontamentos_query(date_from, date_to, None, operator_id, None)
    rows = await db.apontamentos.find(q).to_list(5000)
    users_map = {u["id"]: u["name"] for u in await db.users.find({}, {"password_hash": 0}).to_list(1000)}
    by_op: dict = {}
    for r in rows:
        for oid in r.get("operator_ids", []):
            e = by_op.setdefault(oid, {"operator_id": oid, "operator_name": users_map.get(oid, oid),
                                        "good": 0, "scrap": 0, "run_seconds": 0, "stop_seconds": 0})
            # shared apontamentos: split evenly across operators
            n = max(1, len(r.get("operator_ids", [])))
            e["good"] += r["good"] / n
            e["scrap"] += r["scrap"] / n
            started = datetime.fromisoformat(r["started_at"])
            ended = datetime.fromisoformat(r["closed_at"]) if r.get("closed_at") else datetime.now(timezone.utc)
            total = (ended - started).total_seconds()
            stopped = 0.0
            for s in r.get("stoppages", []):
                s_end = datetime.fromisoformat(s["ended_at"]) if s.get("ended_at") else ended
                stopped += (s_end - datetime.fromisoformat(s["started_at"])).total_seconds()
            e["run_seconds"] += max(0, total - stopped) / n
            e["stop_seconds"] += stopped / n
    out = []
    for e in by_op.values():
        hours = e["run_seconds"] / 3600 or 1
        total = e["good"] + e["scrap"] or 1
        total_time = e["run_seconds"] + e["stop_seconds"] or 1
        out.append({
            **e,
            "good": round(e["good"], 1),
            "scrap": round(e["scrap"], 1),
            "pieces_per_hour": round((e["good"] + e["scrap"]) / hours, 2),
            "scrap_pct": round(e["scrap"] / total * 100, 2),
            "downtime_pct": round(e["stop_seconds"] / total_time * 100, 2),
        })
    return out


@api.get("/reports/aging")
async def report_aging(user=Depends(get_current_user)):
    ops = await db.ops.find({"status": {"$in": ["planned", "in_progress"]}}).to_list(2000)
    now = datetime.now(timezone.utc)
    out = []
    for o in ops:
        try:
            due = datetime.fromisoformat(o["due_date"])
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            days = (due - now).days
        except Exception:
            days = 0
        created = datetime.fromisoformat(o["created_at"])
        age_days = (now - created).days
        pct = (o.get("qty_good", 0) / o["qty_planned"] * 100) if o["qty_planned"] else 0
        out.append({
            "id": o["id"],
            "number": o["number"],
            "client": o["client"],
            "status": o["status"],
            "qty_planned": o["qty_planned"],
            "qty_good": o.get("qty_good", 0),
            "qty_scrap": o.get("qty_scrap", 0),
            "due_date": o["due_date"],
            "days_to_due": days,
            "age_days": age_days,
            "progress_pct": round(pct, 1),
            "overdue": days < 0,
        })
    out.sort(key=lambda x: x["days_to_due"])
    return out


@api.get("/reports/pareto")
async def report_pareto(
    user=Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    machine_id: Optional[str] = None,
):
    q = _filter_apontamentos_query(date_from, date_to, machine_id, None, None)
    rows = await db.apontamentos.find(q).to_list(5000)
    by_reason: dict = {}
    for r in rows:
        for s in r.get("stoppages", []):
            if not s.get("ended_at"):
                continue
            dur = (datetime.fromisoformat(s["ended_at"]) - datetime.fromisoformat(s["started_at"])).total_seconds()
            by_reason[s["reason"]] = by_reason.get(s["reason"], 0) + dur
    items = sorted(
        [{"reason": k, "seconds": int(v), "minutes": round(v / 60, 1)} for k, v in by_reason.items()],
        key=lambda x: x["seconds"], reverse=True,
    )
    total = sum(i["seconds"] for i in items) or 1
    cum = 0
    for it in items:
        cum += it["seconds"]
        it["cumulative_pct"] = round(cum / total * 100, 1)
        it["pct"] = round(it["seconds"] / total * 100, 1)
    return items


@api.get("/reports/export")
async def report_export(
    kind: str,
    format: str = "csv",
    user=Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    machine_id: Optional[str] = None,
    operator_id: Optional[str] = None,
):
    if kind == "oee":
        data = await report_oee(user=user, date_from=date_from, date_to=date_to, machine_id=machine_id)
    elif kind == "productivity":
        data = await report_productivity(user=user, date_from=date_from, date_to=date_to, operator_id=operator_id)
    elif kind == "aging":
        data = await report_aging(user=user)
    elif kind == "pareto":
        data = await report_pareto(user=user, date_from=date_from, date_to=date_to, machine_id=machine_id)
    else:
        raise HTTPException(400, "Tipo de relatório inválido")

    if not data:
        data = [{}]
    headers_keys = list(data[0].keys()) if isinstance(data[0], dict) else []

    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=headers_keys)
        w.writeheader()
        for row in data:
            w.writerow(row)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={kind}.csv"},
        )
    if format == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = kind[:30]
        ws.append(headers_keys)
        for row in data:
            ws.append([row.get(h) for h in headers_keys])
        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        return StreamingResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={kind}.xlsx"},
        )
    raise HTTPException(400, "Formato inválido")


# ---------- Settings ----------
@api.get("/settings")
async def get_settings(user=Depends(get_current_user)):
    s = await db.settings.find_one({"id": "global"})
    return clean(s) if s else {}


@api.put("/settings")
async def upd_settings(payload: dict, user=Depends(require_supervisor)):
    payload.pop("id", None)
    payload.pop("_id", None)
    await db.settings.update_one({"id": "global"}, {"$set": payload}, upsert=True)
    await audit(user["id"], "update", "settings", "global")
    return {"ok": True}


# ---------- Audit log ----------
@api.get("/audit")
async def audit_list(user=Depends(require_supervisor), page: int = 1, page_size: int = 100):
    total = await db.audit_log.count_documents({})
    rows = await db.audit_log.find({}).sort("at", -1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    users_map = {u["id"]: u["name"] for u in await db.users.find({}, {"password_hash": 0}).to_list(1000)}
    for r in rows:
        r["user_name"] = users_map.get(r["user_id"], r["user_id"])
    return {"total": total, "items": [clean(r) for r in rows]}


# ---------- Health ----------
@api.get("/")
async def root():
    return {"ok": True, "app": "ProFab MES/APS"}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
